"""Small asynchronous client for the local WiserLink MPI HTTP API."""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from http.cookies import SimpleCookie
import logging
import re
from typing import Any

from aiohttp import BasicAuth, ClientError, ClientSession

from .const import MPR_INSTANCES_PATH, SEM_IDENTIFICATION_PATH, USAGE_METER_PATH
from .validation import UsageMeterValidationError, validate_usage_meters

_LOGGER = logging.getLogger(__name__)

_WEB_API_KEYWORDS = (
    "MpeEndpoint",
    "MprEndpoint",
    "WirelessManager",
    "WirelessDiagnostic",
    "WirelessMeter",
    "blinkDevice",
    "startCommissioning",
    "stopCommissioning",
    "commissioning",
    "forceRefresh",
    "refresh",
    "requestRead",
    "poll",
)


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
        self._username = username
        self._password = password
        self._auth = BasicAuth(username, password)
        self._webpage_version: str | None = None
        self._web_api_surface: dict[str, Any] | None = None

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

    @staticmethod
    def _analyse_main_javascript(javascript: str) -> dict[str, Any]:
        """Extract explicit Vesta routes and radio-related symbols from main.js."""
        normalized = javascript.replace("\\/", "/")

        version_match = re.search(
            r"WpVersion\s*:\s*[\"']([^\"']+)[\"']", normalized
        )
        version = version_match.group(1) if version_match else "Inconnue"

        routes = sorted(
            {
                match.rstrip(",.)]}")
                for match in re.findall(
                    r"/vesta/[A-Za-z0-9_./;=?:&%+\-,]+", normalized
                )
            }
        )
        methods = sorted(
            {
                route.split("/methods/", 1)[1].split("/", 1)[0]
                for route in routes
                if "/methods/" in route and route.split("/methods/", 1)[1]
            }
        )
        keywords = [
            keyword
            for keyword in _WEB_API_KEYWORDS
            if re.search(re.escape(keyword), normalized, re.IGNORECASE)
        ]

        return {
            "Webpage_Version": version,
            "WebApi_Routes": routes,
            "WebApi_Methods": methods,
            "WebApi_RadioKeywords": keywords,
        }

    async def async_get_mip_identification(self) -> dict[str, Any]:
        """Read MIP identity, firmware and the read-only web API surface."""
        result = await self._request("GET", "/vesta/MipIdentification")
        if not isinstance(result, dict):
            raise WiserLinkError("Réponse MipIdentification invalide")

        if self._web_api_surface is None:
            javascript = await self._request("GET", "/main.js")
            if isinstance(javascript, str):
                self._web_api_surface = self._analyse_main_javascript(javascript)
            else:
                self._web_api_surface = {
                    "Webpage_Version": "Inconnue",
                    "WebApi_Routes": [],
                    "WebApi_Methods": [],
                    "WebApi_RadioKeywords": [],
                }

            self._webpage_version = self._web_api_surface["Webpage_Version"]
            _LOGGER.info(
                "Analyse main.js terminée: %d route(s) Vesta, %d méthode(s), "
                "mots-clés radio=%s",
                len(self._web_api_surface["WebApi_Routes"]),
                len(self._web_api_surface["WebApi_Methods"]),
                self._web_api_surface["WebApi_RadioKeywords"],
            )

        result.update(self._web_api_surface)
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

    @staticmethod
    def _same_mpr_configuration(current: dict[str, Any], payload: dict[str, Any]) -> bool:
        """Return True when a requested MPR update would be a dangerous no-op."""
        for key in ("Id", "Type", "Usage", "PulseWeightUnit", "RfAddress"):
            if str(current.get(key)) != str(payload.get(key)):
                return False
        try:
            return Decimal(str(current.get("PulseWeight"))) == Decimal(
                str(payload.get("PulseWeight"))
            )
        except (InvalidOperation, TypeError, ValueError):
            return False

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
        current = next((item for item in instances if item.get("Id") == meter_id), None)
        payload = {
            "Id": meter_id,
            "Type": meter_type,
            "Usage": usage,
            "PulseWeight": pulse_weight,
            "PulseWeightUnit": pulse_weight_unit,
            "RfAddress": radio_address,
        }

        # Real-device testing showed that even an identical PUT starts the MPR
        # radio configuration procedure and can finish with Param=-1. Never send
        # an existing configuration again when nothing actually changed.
        if current is not None and self._same_mpr_configuration(current, payload):
            _LOGGER.info("Configuration MPR %s inchangée, aucune écriture envoyée", meter_id)
            return {**payload, "_unchanged": True}

        method = "PUT" if current is not None else "POST"
        path = (
            f"/vesta/MpeEndpoint;Id={meter_id}"
            if method == "PUT"
            else MPR_INSTANCES_PATH
        )
        await self._request(method, path, payload)
        await self._async_wait_configuration()
        return {**payload, "_unchanged": False}

    async def async_delete_mpr(self, meter_id: int) -> None:
        """Delete exactly one existing MPR meter."""
        instances = await self.async_get_mpr_instances()
        if not any(item.get("Id") == meter_id for item in instances):
            raise WiserLinkError(f"Compteur MPR {meter_id} introuvable")
        await self._request("DELETE", f"/vesta/MpeEndpoint;Id={meter_id}", {})
        await self._async_wait_configuration()

    async def _async_wait_configuration(self) -> None:
        """Wait up to 120 seconds for an MPR configuration result."""
        for attempt in range(25):
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
            if attempt < 24:
                await asyncio.sleep(5)
        raise WiserLinkError("Délai dépassé pendant la configuration MPR")

    async def async_reboot(self) -> None:
        """Reboot the EER31600 through the authenticated Web session API."""
        try:
            async with self._session.post(
                f"{self._base_url}/rs/login",
                json={"username": self._username, "password": self._password},
                timeout=10,
            ) as response:
                if response.status in (401, 403):
                    raise WiserLinkAuthError("Identifiants refusés par le MPI")
                if response.status >= 400:
                    body = (await response.text())[:300]
                    raise WiserLinkError(
                        f"Connexion session Web refusée HTTP {response.status}: {body}"
                    )

                sid = response.cookies.get("SID")
                sid_value = sid.value if sid is not None else None
                if not sid_value:
                    cookie = SimpleCookie()
                    for header in response.headers.getall("Set-Cookie", []):
                        cookie.load(header)
                    if "SID" in cookie:
                        sid_value = cookie["SID"].value
                await response.read()

            if not sid_value:
                raise WiserLinkError("Le MPI n'a pas renvoyé de cookie SID")

            async with self._session.post(
                f"{self._base_url}/rs/Device/methods/Reboot",
                auth=self._auth,
                cookies={"SID": sid_value},
                json={},
                timeout=10,
            ) as response:
                if response.status in (401, 403):
                    raise WiserLinkAuthError("Session de redémarrage refusée par le MPI")
                if response.status >= 400:
                    body = (await response.text())[:300]
                    raise WiserLinkError(
                        f"Redémarrage refusé HTTP {response.status}: {body}"
                    )
                await response.read()
        except WiserLinkError:
            raise
        except (ClientError, TimeoutError) as err:
            raise WiserLinkError(str(err)) from err

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
