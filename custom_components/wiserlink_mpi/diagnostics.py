"""Diagnostics for WiserLink MPI."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_RADIO_ROUTE_TERMS = (
    "mpe",
    "mpr",
    "wireless",
    "radio",
    "commission",
    "blink",
    "refresh",
    "poll",
    "read",
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return a privacy-safe snapshot focused on the local web API surface."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    mip = data.get("_mip_identification", {})

    routes = list(mip.get("WebApi_Routes", []))
    radio_candidates = [
        route
        for route in routes
        if any(term in route.lower() for term in _RADIO_ROUTE_TERMS)
    ]

    return {
        "webpage_version": mip.get("Webpage_Version"),
        "web_api": {
            "route_count": len(routes),
            "routes": routes,
            "methods": list(mip.get("WebApi_Methods", [])),
            "radio_keywords": list(mip.get("WebApi_RadioKeywords", [])),
            "radio_candidate_routes": radio_candidates,
        },
    }
