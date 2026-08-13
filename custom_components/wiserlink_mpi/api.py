"""Small asynchronous client for the local WiserLink MPI HTTP API."""

from __future__ import annotations

from typing import Any

from aiohttp import BasicAuth, ClientError, ClientSession

from .const import USAGE_METER_PATH


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

    async def async_get_usage_meters(self) -> dict[str, Any]:
        """Read all meters in one request."""
        result = await self._request("GET", USAGE_METER_PATH)
        if not isinstance(result, dict) or not isinstance(result.get("UsageMeterList"), list):
            raise WiserLinkError("Réponse UsageMeter invalide")
        return result

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
