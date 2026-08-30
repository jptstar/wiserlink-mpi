"""Pure helpers for repairing WiserLink meter entity identities.

This module deliberately has no Home Assistant imports so the dangerous entity
registry migration can be regression-tested with the exact failure modes seen
on real installations.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import re
from typing import Any, Mapping, Sequence

from .meter import is_water_meter, meter_api_name, meter_identity

_METRICS = ("energyconsumed", "power")
_ENTITY_ID_SUFFIX_RE = re.compile(r"_\d+$")
_LABEL_SUFFIX_RE = re.compile(r"\s+(?:énergie|energie|puissance|volume)$", re.IGNORECASE)


def split_meter_unique_id(
    prefix: str, unique_id: str | None
) -> tuple[str | None, int | None, str | None]:
    """Return ``(identity, legacy_index, metric)`` for one meter unique id."""
    if not unique_id:
        return None, None, None
    start = f"{prefix}_"
    if not unique_id.startswith(start):
        return None, None, None
    rest = unique_id[len(start) :]
    for metric in _METRICS:
        suffix = f"_{metric}"
        if not rest.endswith(suffix):
            continue
        token = rest[: -len(suffix)]
        if not token:
            return None, None, None
        if token.isdigit():
            return None, int(token), metric
        return token, None, metric
    return None, None, None


def _entry_text(item: Mapping[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("entity_id", "name", "original_name")
    ).lower()


def _device_class(item: Mapping[str, Any]) -> str:
    return str(
        item.get("device_class") or item.get("original_device_class") or ""
    ).lower()


def infer_identity(
    prefix: str,
    item: Mapping[str, Any],
    meters: Sequence[Mapping[str, Any]],
) -> str | None:
    """Infer one meter identity from strong evidence only.

    Current API names intentionally win over an already-semantic unique id. This
    repairs identities that were assigned incorrectly by the 0.8.17-0.8.19
    migrations while preserving user-facing entity_ids.
    """
    text = _entry_text(item)
    device_class = _device_class(item)

    # Strongest evidence: a name configured in the Wiser itself.
    for meter in meters:
        api_name = meter_api_name(meter).strip().lower()
        if api_name and api_name in text:
            return meter_identity(meter)

    if device_class == "gas" or re.search(r"\b(?:gaz|gas)\b", text):
        return "gas_meter"

    if device_class == "water":
        water_ids = {meter_identity(meter) for meter in meters if is_water_meter(meter)}
        if "chaud" in text or "hot" in text:
            return "hot_water_meter"
        if "froid" in text or "cold" in text:
            return "cold_water_meter"
        if len(water_ids) == 1:
            return next(iter(water_ids))
        return "water_meter"

    if any(
        word in text
        for word in (
            "compteur électrique",
            "compteur electrique",
            "electricity meter",
            "teleinfo",
            "téléinfo",
            " tic ",
        )
    ):
        return "electricity_meter"

    for number in range(1, 6):
        if re.search(rf"\b(?:ct|load)\s*{number}\b", text):
            return f"load{number}"

    semantic_words = (
        (("autres", "others"), "others"),
        (("climatisation", "cooling"), "cooling"),
        (("prises", "sockets"), "sockets"),
        (("chauffage", "heating"), "heating"),
        (("eau chaude", "hot water"), "hot_water"),
    )
    for words, identity in semantic_words:
        if any(word in text for word in words):
            return identity

    identity, _legacy_index, metric = split_meter_unique_id(
        prefix, str(item.get("unique_id") or "")
    )
    return identity if metric else None


def _created_rank(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return float("inf")


def _survivor_rank(prefix: str, item: Mapping[str, Any]) -> tuple[Any, ...]:
    """Prefer the historical entity and never prefer a newly-created ``_2``."""
    _identity, legacy_index, _metric = split_meter_unique_id(
        prefix, str(item.get("unique_id") or "")
    )
    entity_id = str(item.get("entity_id") or "")
    return (
        _created_rank(item.get("created_at")),
        0 if legacy_index is not None else 1,
        1 if _ENTITY_ID_SUFFIX_RE.search(entity_id) else 0,
        entity_id,
    )


def display_base_name(item: Mapping[str, Any]) -> str | None:
    """Return the historical integration name without metric suffix."""
    value = str(item.get("original_name") or "").strip()
    if not value:
        return None
    value = _LABEL_SUFFIX_RE.sub("", value).strip()
    return value or None


def build_repair_plan(
    prefix: str,
    entries: Sequence[Mapping[str, Any]],
    meters: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Build deterministic registry repairs without mutating Home Assistant."""
    meter_entries: list[Mapping[str, Any]] = []
    parsed: dict[str, tuple[str | None, int | None, str | None]] = {}
    legacy_groups: dict[int, list[Mapping[str, Any]]] = defaultdict(list)

    for item in entries:
        entity_id = str(item.get("entity_id") or "")
        parts = split_meter_unique_id(prefix, str(item.get("unique_id") or ""))
        identity, legacy_index, metric = parts
        if metric is None:
            continue
        meter_entries.append(item)
        parsed[entity_id] = parts
        if legacy_index is not None:
            legacy_groups[legacy_index].append(item)

    # Recover legacy numeric slots from strong registry/API evidence.
    index_to_identity: dict[int, str] = {}
    for index, group in legacy_groups.items():
        identities = {
            identity
            for item in group
            if (identity := infer_identity(prefix, item, meters)) is not None
        }
        if len(identities) == 1:
            index_to_identity[index] = next(iter(identities))

    # Exact elimination is allowed only for the five historical CT slots.
    mapped_loads = {
        identity for identity in index_to_identity.values() if identity.startswith("load")
    }
    missing_loads = {f"load{number}" for number in range(1, 6)} - mapped_loads
    unresolved_ct_slots = [
        index
        for index in sorted(legacy_groups)
        if index < 5 and index not in index_to_identity
    ]
    if len(missing_loads) == 1 and len(unresolved_ct_slots) == 1:
        index_to_identity[unresolved_ct_slots[0]] = next(iter(missing_loads))

    candidates: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for item in meter_entries:
        entity_id = str(item.get("entity_id") or "")
        direct_identity, legacy_index, metric = parsed[entity_id]
        assert metric is not None

        inferred = infer_identity(prefix, item, meters)
        if legacy_index is not None and legacy_index in index_to_identity:
            identity = inferred or index_to_identity[legacy_index]
        else:
            identity = inferred or direct_identity
        if identity is None:
            continue
        candidates[(identity, metric)].append(item)

    plans: list[dict[str, Any]] = []
    for (identity, metric), group in sorted(candidates.items()):
        survivor = min(group, key=lambda item: _survivor_rank(prefix, item))
        survivor_id = str(survivor.get("entity_id") or "")
        plans.append(
            {
                "identity": identity,
                "metric": metric,
                "canonical_unique_id": f"{prefix}_{identity}_{metric}",
                "survivor_entity_id": survivor_id,
                "remove_entity_ids": sorted(
                    str(item.get("entity_id") or "")
                    for item in group
                    if str(item.get("entity_id") or "") != survivor_id
                ),
                "suggested_name": display_base_name(survivor),
                "survivor_was_legacy": parsed[survivor_id][1] is not None,
                "candidate_count": len(group),
            }
        )

    return plans, index_to_identity
