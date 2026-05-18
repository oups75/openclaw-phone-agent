from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import HTTPException

from app.settings import Settings

LOCAL_TARGET_ALIASES = {
    "me",
    "my phone",
    "my mobile",
    "my cell",
    "computer",
    "speaker",
    "mic",
    "human-softphone",
}

ENDPOINT_PREFIX_RE = re.compile(r"^[A-Za-z0-9_.-]+/")
PHONE_NUMBER_RE = re.compile(r"^[+\d][\d\s().-]*$")


def resolve_outbound_endpoint(target: str, settings: Settings) -> str:
    normalized = _normalize_target(target)
    if normalized in LOCAL_TARGET_ALIASES:
        return settings.softphone_endpoint

    if ENDPOINT_PREFIX_RE.match(target):
        return target

    phonebook = _load_phonebook(settings.phonebook_path)
    if phonebook:
        for key in _phonebook_keys(normalized, target):
            entry = phonebook.get(key)
            if entry is None:
                continue
            endpoint = _extract_endpoint(entry)
            if endpoint:
                return endpoint

    if PHONE_NUMBER_RE.match(target):
        if not settings.outbound_pstn_context:
            raise HTTPException(
                status_code=400,
                detail="No PSTN context configured for phone numbers.",
            )
        number = _sanitize_phone_number(target)
        return settings.outbound_number_template.format(
            number=number, context=settings.outbound_pstn_context
        )

    raise HTTPException(status_code=404, detail=f"Unknown contact or endpoint: {target}")


def _normalize_target(target: str) -> str:
    return " ".join(target.strip().lower().split())


def _sanitize_phone_number(target: str) -> str:
    return re.sub(r"[^\d+]", "", target)


def _load_phonebook(path: Path | None) -> dict[str, object]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("contacts"), dict):
        data = data["contacts"]
    if not isinstance(data, dict):
        return {}
    return data


def _extract_endpoint(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        endpoint = entry.get("endpoint")
        if isinstance(endpoint, str) and endpoint.strip():
            return endpoint.strip()
    return None


def _phonebook_keys(normalized: str, raw: str) -> list[str]:
    keys = [normalized]
    raw_key = raw.strip()
    if raw_key and raw_key not in keys:
        keys.append(raw_key)
    return keys
