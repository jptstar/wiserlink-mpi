"""Small asynchronous client for the local WiserLink MPI HTTP API."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from aiohttp import BasicAuth, ClientError, ClientSession

from .const import MPR_INSTANCES_PATH, SEM_IDENTIFICATION_PATH, USAGE_METER_PATH
from .validation import UsageMeterValidationError, validate_usage_meters


class WiserLinkError(Exception):
    """Base API error."""


class WiserLinkAuthError(WiserLinkError):
    """Authentication failed."""


class WiserLinkClient:
    """Talk to one MPI over the local network."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._base_url = f"http://{host}:{port}"
        self._auth = BasicAuth(username, password)
        self._webpage_version: str | None = None

    async def async_get_usage_meters(self) -> dict[str, Any]:
        """Read and validate all meters in one request."""
        result = await self._request("GET", USAGE_METER_PATH)
        if not isinstance(result, dict) or not isinstance(
            result.get("UsageMeterList"), list
        ):
            raise WiserLinkError("Réponse UsageMeter invalide")
        try:
            validate_usage_meters(result["UsageMeterList"])
        except UsageMeterValidationError as err:
            raise WiserLinkError(str(err)) from err
        return result

    async def async_get_sem_identification(self) -> dict[str, Any]:
        """Read EM5, MIP and electricity-meter communication status."""
        result = await self._request("GET", SEM_IDENTIFICATION_PATH)
        if not isinstance(result, dict):
            raise WiserLinkError("Réponse SemIdentification invalide")
        return result

    async def async_get_mip_identification(self) -> dict[str, Any]:
        """Read MIP identity and firmware information."""
        result = await self._request("GET", "/vesta/MipIdentification")
        if not isinstance(result, dict):
            raise WiserLinkError("Réponse MipIdentification invalide")
        if self._webpage_version is None:
            javascript = await self._request("GET", "/main.js")
            if isinstance(javascript, str):
                match = re.search(r'WpVersion:"([^"]+)"', javascript)
                self._webpage_version = match.group(1) if match else "Inconnue"
            else:
                self._webpage_version = "Inconnue"
        result["Webpage_Version"] = self._webpage_version
        return result

    async def async_get_mpr_instances(self) -> list[dict[str, Any]]:
        """Read configured MPR pulse meters."""
        result = await self._request("GET", MPR_INSTANCES_PATH)
        if not isinstance(result, list) or not all(
            isinstance(item, dict) for item in result
        ):
            raise WiserLinkError("Réponse MPR invalide")
        return result

    async def async_get_events(self) -> dict[str, Any]:
        """Read the first page containing the most recent MPI events."""
        result = await self._request("GET", "/vesta/EventList")
        if not isinstance(result, dict) or not isinstance(
            result.get("EventList"), list
        ):
            raise WiserLinkError("Réponse EventList invalide")
        return result

    async def async_configure_mpr(
        self,
        meter_id: int,
        meter_type: str,
        usage: str,
        pulse_weight: float,
        pulse_weight_unit: str,
        radio_address: str,
    ) -> dict[str, Any]:
        """Create or update one MPR meter using the official web UI format."""
        instances = await self.async_get_mpr_instances()
        method = (
            "PUT"
            if any(item.get("Id") == meter_id for item in instances)
            else "POST"
        )
        path = (
            f"/vesta/MpeEndpoint;Id={meter_id}"
            if method == "PUT"
            else MPR_INSTANCES_PATH
        )
        payload = {
            "Id": meter_id,
            "Type": meter_type,
            "Usage": usage,
            "PulseWeight": pulse_weight,
            "PulseWeightUnit": pulse_weight_unit,
            "RfAddress": radio_address,
        }
        await self._request(method, path, payload)
        await self._async_wait_configuration()
        return payload

    async def async_delete_mpr(self, meter_id: int) -> None:
        """Delete exactly one existing MPR meter."""
        instances = await self.async_get_mpr_instances()
        if not any(item.get("Id") == meter_id for item in instances):
            raise WiserLinkError(f"Compteur MPR {meter_id} introuvable")
        await self._request("DELETE", f"/vesta/MpeEndpoint;Id={meter_id}", {})
        await self._async_wait_configuration()

    async def _async_wait_configuration(self) -> None:
        """Wait for the MPI to apply an MPR configuration change."""
        for attempt in range(7):
            result = await self._request(
                "POST",
                "/vesta/Firmware/methods/checkSubmitAction",
                {"Param1": 0, "Param2": 0},
            )
            status = result.get("Param") if isinstance(result, dict) else None
            if status == 1:
                return
            if status == -1:
                raise WiserLinkError("Le MPI a refusé la configuration MPR")
            if attempt < 6:
                await asyncio.sleep(5)
        raise WiserLinkError("Délai dépassé pendant la configuration MPR")

    async def async_send_command(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> Any:
        """Send an explicitly requested write operation to a Vesta endpoint."""
        method = method.upper()
        if method not in {"POST", "PUT", "PATCH"}:
            raise WiserLinkError("Seules les méthodes POST, PUT et PATCH sont permises")
        if not path.startswith("/vesta/") or "://" in path or ".." in path:
            raise WiserLinkError("Le chemin doit commencer par /vesta/")
        return await self._request(method, path, payload)

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                auth=self._auth,
                json=payload,
                timeout=10,
            ) as response:
                if response.status in (401, 403):
                    raise WiserLinkAuthError("Identifiants refusés par le MPI")
                if response.status >= 400:
                    body = (await response.text())[:300]
                    raise WiserLinkError(f"Erreur HTTP {response.status}: {body}")
                if response.status == 204:
                    return None
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type:
                    return await response.json(content_type=None)
                text = await response.text()
                return text or None
        except WiserLinkError:
            raise
        except (ClientError, TimeoutError) as err:
            raise WiserLinkError(str(err)) from err
