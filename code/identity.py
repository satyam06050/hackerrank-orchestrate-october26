"""Resolve request emails and reconstruct identifiers at an audit cutoff."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pandas as pd

from data_layer import index_audit_log, index_identities


@dataclass(frozen=True)
class Identifier:
    value: str
    field: str
    valid_from: str
    valid_to: str
    source_id: str


@dataclass(frozen=True)
class IdentitySet:
    user_id: str
    identifiers: tuple[Identifier, ...]
    resolved_via_history: bool


@dataclass(frozen=True)
class Unresolved:
    reason: str
    matching_user_ids: tuple[str, ...] = ()


_TRACKED_FIELDS = ("email", "phone")


def _norm_time(value: str) -> str:
    return str(value).replace("+00:00", "Z")


def _reconstruct(user_id: str, current: dict[str, Any], events: list[dict[str, Any]], cutoff: str) -> tuple[Identifier, ...]:
    cutoff = _norm_time(cutoff)
    by_field = {field: [] for field in _TRACKED_FIELDS}
    for event in events:
        if event["field_name"] in by_field:
            by_field[event["field_name"]].append(event)
    for rows in by_field.values():
        rows.sort(key=lambda row: (_norm_time(row["event_time"]), row["audit_id"]))

    result: list[Identifier] = []
    for field, rows in by_field.items():
        # Emit values that ended at an in-scope change.
        for i, event in enumerate(rows):
            ended = _norm_time(event["event_time"])
            old = event["old_value"]
            if ended > cutoff or not old:
                continue
            start = ""
            for previous in reversed(rows[:i]):
                if previous["new_value"].casefold() == old.casefold():
                    start = _norm_time(previous["event_time"])
                    break
            result.append(Identifier(old, field, start, ended, event["audit_id"]))

        # Rewind the current snapshot through post-cutoff changes.
        value = str(current.get(field, ""))
        source = f"identity:{user_id}"
        start = ""
        for event in rows:
            if event["new_value"].casefold() == value.casefold():
                start = _norm_time(event["event_time"])
        end = ""
        for event in reversed(rows):
            when = _norm_time(event["event_time"])
            if when > cutoff and value.casefold() == event["new_value"].casefold():
                value = event["old_value"]
                source = event["audit_id"]
                start = ""
                end = when
            elif when <= cutoff and event["new_value"].casefold() == value.casefold():
                if not start or start > cutoff:
                    start = when
                end = ""
        if value and (not start or start <= cutoff):
            result.append(Identifier(value, field, start, end, source))

    # Current email_hash values verify as SHA-256(email); derive hashes for included emails.
    for item in list(result):
        if item.field == "email":
            hashed = sha256(item.value.encode("utf-8")).hexdigest()
            result.append(Identifier(hashed, "email_hash", item.valid_from, item.valid_to, item.source_id))

    unique = {(x.value, x.field, x.valid_from, x.valid_to, x.source_id): x for x in result}
    return tuple(sorted(unique.values(), key=lambda x: (x.field, x.valid_from, x.value.casefold())))


def _resolve_frames(email: str, audit_at: str, identities: pd.DataFrame, audit: pd.DataFrame) -> IdentitySet | Unresolved:
    query = email.strip().casefold()
    current_matches = {
        row["user_id"] for row in identities.to_dict(orient="records")
        if row["email"].strip().casefold() == query
    }
    historical_matches = {
        row["user_id"] for row in audit.to_dict(orient="records")
        if row["field_name"] == "email"
        and query in (row["old_value"].strip().casefold(), row["new_value"].strip().casefold())
    }
    matches = current_matches | historical_matches
    if not matches:
        return Unresolved("zero_matches")
    if len(matches) != 1:
        return Unresolved("multiple_matches", tuple(sorted(matches)))
    user_id = next(iter(matches))
    rows = identities.loc[identities["user_id"] == user_id].to_dict(orient="records")
    if len(rows) != 1:
        return Unresolved("multiple_matches", tuple(sorted(matches)))
    history = audit.loc[audit["user_id"] == user_id].to_dict(orient="records")
    return IdentitySet(user_id, _reconstruct(user_id, rows[0], history, audit_at), user_id not in current_matches)


def resolve(email: str, audit_at: str) -> IdentitySet | Unresolved:
    """Resolve email case-insensitively; never select among zero or multiple users."""
    global _IDENTITIES_FRAME, _AUDIT_FRAME
    if _IDENTITIES_FRAME is None:
        index_identities()
        index_audit_log()
        from data_layer import _LOADED_FRAMES

        _IDENTITIES_FRAME = _LOADED_FRAMES["identities"]
        _AUDIT_FRAME = _LOADED_FRAMES["audit_log"]
    return _resolve_frames(email, audit_at, _IDENTITIES_FRAME, _AUDIT_FRAME)


_IDENTITIES_FRAME: pd.DataFrame | None = None
_AUDIT_FRAME: pd.DataFrame | None = None
