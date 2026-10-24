"""Deterministic extraction of structured evidence into schema Observations."""

import json
import re
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from config import DATASET_DIR  # noqa: E402
from data_layer import index_activity, index_audit_log, load_manifest  # noqa: E402
from schema import Authority, EvidenceKind, Observation, Scope  # noqa: E402


REGISTRY_PATH = Path(__file__).resolve().parent / "source_registry.json"
_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)")
_KV_RE = re.compile(r'''([A-Za-z_][\w.-]*)=(?:"((?:\\.|[^"])*)"|'([^']*)'|([^\s]+))''')
_TABLE_SYSTEMS = {
    "customer_profiles": "account_service",
    "customer_profiles_primary": "account_service",
    "customer_profiles_failover_replica": "account_service",
    "customer_search_documents": "search_service",
    "search_documents_primary": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}
_FULL_DUMP_MARKER = re.compile(
    r"(?:complete\s+(?:database\s+)?dump|full\s+(?:database\s+)?dump|"
    r"all\s+rows\s+included|completeness\s*[:=]\s*(?:complete|full)|"
    r"row_count\s*[:=]\s*\d+\s*/\s*\d+)",
    re.IGNORECASE,
)


def _normal_resource(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _observation_dict(observation: Observation) -> dict[str, Any]:
    result = asdict(observation)
    result["evidence_kind"] = observation.evidence_kind.value
    result["authority"] = observation.authority.value
    result["scope"] = observation.scope.value
    return result


def _rows_by_index(index: dict[str, dict[str, list[dict[str, Any]]]], key: str) -> list[dict[str, Any]]:
    rows = [row for group in index[key].values() for row in group]
    return rows


def _file_scope(sql_text: str) -> tuple[Scope, str]:
    header_metadata = "\n".join(
        line for line in sql_text.splitlines() if line.lstrip().startswith(("--", "/*", "*"))
    )
    if _FULL_DUMP_MARKER.search(header_metadata):
        return Scope.FULL_TABLE, "The SQL file explicitly marks the dump/table coverage as complete."
    return (
        Scope.PARTIAL,
        "The SQL file contains no explicit complete/full-dump marker or covered-versus-total row count; "
        "only the rows inserted in this file are treated as captured.",
    )


def _sql_observations(file_id: str, file_path: Path, captured_at: str) -> tuple[list[Observation], dict[str, Any]]:
    sql_text = file_path.read_text(encoding="utf-8-sig")
    scope, scope_reason = _file_scope(sql_text)
    connection = sqlite3.connect(":memory:")
    observations: list[Observation] = []
    systems: set[str] = set()
    row_counts: dict[str, int] = {}
    try:
        connection.executescript(sql_text)
        table_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for table_name in table_names:
            quoted = '"' + table_name.replace('"', '""') + '"'
            cursor = connection.execute(f"SELECT * FROM {quoted}")
            columns = [column[0] for column in cursor.description or ()]
            rows = cursor.fetchall()
            row_counts[table_name] = len(rows)
            for row_number, values in enumerate(rows, start=1):
                fields = dict(zip(columns, values))
                nested: dict[str, Any] = {}
                if isinstance(fields.get("record_json"), str):
                    try:
                        candidate = json.loads(fields["record_json"])
                        if isinstance(candidate, dict):
                            nested = candidate
                            fields["record_fields"] = candidate
                    except json.JSONDecodeError:
                        # Preserve the supplied record_json text as observed.
                        pass
                for key in ("resource_id", "user_id"):
                    if (fields.get(key) is None or fields.get(key) == "") and nested.get(key) is not None:
                        fields[key] = nested[key]
                fields["table_name"] = table_name
                system_id = str(
                    fields.get("origin_system")
                    or fields.get("data_store")
                    or fields.get("system")
                    or nested.get("system")
                    or _TABLE_SYSTEMS.get(table_name, "unknown")
                )
                if system_id != "unknown":
                    systems.add(system_id)
                resource_id = _normal_resource(fields.get("resource_id") or nested.get("resource_id"))
                user_id = fields.get("user_id") or nested.get("user_id")
                if user_id not in (None, ""):
                    fields["user_id"] = user_id
                observations.append(
                    Observation(
                        obs_id=f"{file_id}:{table_name}:{row_number}",
                        file_id=file_id,
                        location=f"{table_name}[{row_number}]",
                        system_id=system_id,
                        resource_id=resource_id,
                        evidence_kind=EvidenceKind.TABLE_SNAPSHOT,
                        authority=Authority.AUTHORITATIVE,
                        scope=scope,
                        fields_observed=fields,
                        assertion=f"Row present in SQL table {table_name}",
                        observed_at=captured_at,
                        extraction_method="sqlite3 in-memory SQL execution",
                    )
                )
    finally:
        connection.close()
    return observations, {
        "systems_covered": sorted(systems),
        "tables_covered": table_names,
        "table_row_counts": row_counts,
        "scope": scope.value,
        "scope_decision_reason": scope_reason,
    }


def _parse_log_line(line: str) -> tuple[str | None, dict[str, Any]]:
    timestamp_match = _ISO_RE.match(line)
    event_time = timestamp_match.group(1) if timestamp_match else None
    remainder = line[timestamp_match.end():].strip() if timestamp_match else line
    parsed: dict[str, Any] = {}
    candidate = remainder
    if candidate.startswith("{"):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                parsed.update(obj)
                return event_time, parsed
        except json.JSONDecodeError:
            pass
    for match in _KV_RE.finditer(remainder):
        key, double_quoted, single_quoted, bare = match.groups()
        value = double_quoted if double_quoted is not None else single_quoted if single_quoted is not None else bare
        parsed[key] = value
    if not parsed:
        parsed["message"] = remainder
    return event_time, parsed


def _log_observations(file_id: str, file_path: Path, captured_at: str) -> tuple[list[Observation], dict[str, Any]]:
    observations: list[Observation] = []
    systems: set[str] = set()
    with file_path.open("r", encoding="utf-8-sig", errors="strict") as stream:
        for line_number, line in enumerate(stream, start=1):
            raw = line.rstrip("\r\n")
            if not raw.strip():
                continue
            event_time, fields = _parse_log_line(raw)
            resource_id = _normal_resource(fields.get("resource_id"))
            system_id = str(fields.get("service") or fields.get("system") or fields.get("logger") or "unknown")
            if system_id != "unknown":
                systems.add(system_id)
            fields["raw_line"] = raw
            observations.append(
                Observation(
                    obs_id=f"{file_id}:line:{line_number}",
                    file_id=file_id,
                    location=f"line:{line_number}",
                    system_id=system_id,
                    resource_id=resource_id,
                    evidence_kind=EvidenceKind.EVENT,
                    authority=Authority.CLAIM,
                    scope=Scope.EVENT,
                    fields_observed=fields,
                    assertion=str(fields.get("event") or fields.get("message") or "Log line recorded"),
                    observed_at=event_time or captured_at,
                    extraction_method="deterministic timestamp and key/value log parsing",
                )
            )
    return observations, {"systems_covered": sorted(systems), "tables_covered": []}


def _json_kind(filename: str, payload: dict[str, Any]) -> EvidenceKind:
    lower_name = filename.lower()
    if any(marker in lower_name for marker in ("connector", "configuration", "config")):
        return EvidenceKind.CONFIG
    if "export" in lower_name or "service_exports" in payload:
        return EvidenceKind.EXPORT_MANIFEST
    if "audit" in lower_name or "security_audit" in str(payload.get("origin", "")).lower():
        return EvidenceKind.EVENT
    return EvidenceKind.TABLE_SNAPSHOT


def _json_records(payload: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield context system and each record object, retaining original fields."""
    snapshots = payload.get("snapshots")
    if isinstance(snapshots, list):
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            context = str(snapshot.get("data_store") or "unknown")
            for record in snapshot.get("records", []):
                if isinstance(record, dict):
                    fields = dict(record)
                    fields.setdefault("data_store", snapshot.get("data_store", ""))
                    fields.setdefault("collection", snapshot.get("collection", ""))
                    yield context, fields
        return

    records = payload.get("records")
    if isinstance(records, list):
        context = str(payload.get("source") or payload.get("origin") or payload.get("collection") or "unknown")
        for record in records:
            if isinstance(record, dict):
                yield str(record.get("system") or context), dict(record)
        return

    service_exports = payload.get("service_exports")
    if isinstance(service_exports, dict):
        for system, service_data in service_exports.items():
            if not isinstance(service_data, dict):
                continue
            for section, entries in service_data.items():
                if isinstance(entries, list):
                    for record in entries:
                        if isinstance(record, dict):
                            fields = dict(record)
                            fields.setdefault("exported_system", system)
                            fields.setdefault("export_section", section)
                            yield str(system), fields


def _json_observations(
    file_id: str, file_path: Path, captured_at: str
) -> tuple[list[Observation], dict[str, Any], list[str]]:
    payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("top-level JSON value must be an object")
    file_kind = _json_kind(file_path.name, payload)
    observations: list[Observation] = []
    systems: set[str] = set()
    errors: list[str] = []
    missing_enabled = 0
    records = list(_json_records(payload))
    for ordinal, (context_system, fields) in enumerate(records, start=1):
        system_id = str(fields.get("system") or fields.get("data_store") or fields.get("exported_system") or context_system)
        if system_id not in ("", "unknown"):
            systems.add(system_id)
        if file_kind == EvidenceKind.CONFIG and "enabled" not in fields:
            missing_enabled += 1
        observation_kind = file_kind
        scope = Scope.EXPORT_LISTED if file_kind == EvidenceKind.EXPORT_MANIFEST else Scope.PARTIAL
        if file_kind == EvidenceKind.EXPORT_MANIFEST:
            # Connector rows embedded in service resource exports are configuration evidence.
            if "enabled" in fields and ("connector" in str(fields.get("export_section", "")).lower() or "connector" in str(fields.get("resource_id", "")).lower()):
                observation_kind = EvidenceKind.CONFIG
                scope = Scope.PARTIAL
        resource_id = _normal_resource(fields.get("resource_id"))
        observed_at = captured_at
        observations.append(
            Observation(
                obs_id=f"{file_id}:json:{ordinal}",
                file_id=file_id,
                location=f"records[{ordinal - 1}]",
                system_id=system_id,
                resource_id=resource_id,
                evidence_kind=observation_kind,
                authority=Authority.CORROBORATING if observation_kind == EvidenceKind.EXPORT_MANIFEST else Authority.AUTHORITATIVE,
                scope=scope,
                fields_observed=fields,
                assertion=f"JSON record listed for {system_id}",
                observed_at=observed_at,
                extraction_method="deterministic JSON record traversal",
            )
        )
    if file_kind == EvidenceKind.CONFIG and not records:
        errors.append("CONFIG file contains no record objects")
    elif missing_enabled:
        errors.append(f"{missing_enabled} CONFIG records have no enabled flag")
    return observations, {
        "kind": file_kind.value,
        "systems_covered": sorted(systems),
        "tables_covered": [],
    }, errors


def _csv_observations() -> tuple[list[Observation], dict[str, Any]]:
    observations: list[Observation] = []

    activity_rows = _rows_by_index(index_activity(), "by_resource_id")
    activity_rows.sort(key=lambda row: (row.get("event_time", ""), row.get("activity_id", "")))
    for row in activity_rows:
        activity_id = row.get("activity_id", "")
        observations.append(
            Observation(
                obs_id=f"system_activity:{activity_id}",
                file_id="system_activity.csv",
                location=f"activity_id={activity_id}",
                system_id=row.get("source_system") or row.get("target_system") or "unknown",
                resource_id=_normal_resource(row.get("resource_id")),
                evidence_kind=EvidenceKind.EVENT,
                authority=Authority.CLAIM,
                scope=Scope.EVENT,
                fields_observed=row,
                assertion=f"Activity event {row.get('event_type', '')}: {row.get('reported_result', '')}",
                observed_at=row.get("event_time", ""),
                extraction_method="CSV row mapping",
            )
        )

    audit_rows = _rows_by_index(index_audit_log(), "by_user_id")
    audit_rows.sort(key=lambda row: (row.get("event_time", ""), row.get("audit_id", "")))
    for row in audit_rows:
        audit_id = row.get("audit_id", "")
        observations.append(
            Observation(
                obs_id=f"user_audit_log:{audit_id}",
                file_id="user_audit_log.csv",
                location=f"audit_id={audit_id}",
                system_id="identity_service",
                resource_id=None,
                evidence_kind=EvidenceKind.IDENTITY_CHANGE,
                authority=Authority.AUTHORITATIVE,
                scope=Scope.EVENT,
                fields_observed=row,
                assertion=f"Identity field {row.get('field_name', '')} changed",
                observed_at=row.get("event_time", ""),
                extraction_method="CSV row mapping",
            )
        )
    return observations, {
        "system_activity.csv": {
            "kind": EvidenceKind.EVENT.value,
            "path": "system_activity.csv",
            "parse_status": "ok",
            "systems_covered": sorted({o.system_id for o in observations if o.file_id == "system_activity.csv"}),
            "tables_covered": [],
            "observation_count": len(activity_rows),
            "errors": [],
        },
        "user_audit_log.csv": {
            "kind": EvidenceKind.IDENTITY_CHANGE.value,
            "path": "user_audit_log.csv",
            "parse_status": "ok",
            "systems_covered": ["identity_service"],
            "tables_covered": [],
            "observation_count": len(audit_rows),
            "errors": [],
        },
    }


def extract_all(
    registry_path: str | Path = REGISTRY_PATH,
) -> tuple[list[Observation], dict[str, dict[str, Any]]]:
    """Extract supported files, write source registry, and reject parse failures."""
    manifest = load_manifest()
    observations: list[Observation] = []
    registry: dict[str, dict[str, Any]] = {}

    for item in manifest.to_dict(orient="records"):
        file_id = item["file_id"]
        file_path = Path(DATASET_DIR) / item["file_path"]
        capture_time = item["captured_at"]
        suffix = file_path.suffix.lower()
        kind = {
            ".sql": EvidenceKind.TABLE_SNAPSHOT.value,
            ".log": EvidenceKind.EVENT.value,
            ".json": "JSON",
            ".png": EvidenceKind.SCREENSHOT.value,
            ".pdf": EvidenceKind.DOCUMENT.value,
            ".txt": EvidenceKind.DOCUMENT.value,
        }.get(suffix, "UNSUPPORTED")
        entry: dict[str, Any] = {
            "kind": kind,
            "path": item["file_path"],
            "parse_status": "ok",
            "systems_covered": [],
            "tables_covered": [],
            "observation_count": 0,
            "errors": [],
        }
        try:
            if not file_path.is_file():
                raise FileNotFoundError(f"manifest path not found: {file_path}")
            if suffix == ".sql":
                file_observations, details = _sql_observations(file_id, file_path, capture_time)
                entry.update(details)
            elif suffix == ".log":
                file_observations, details = _log_observations(file_id, file_path, capture_time)
                entry.update(details)
            elif suffix == ".json":
                file_observations, details, errors = _json_observations(file_id, file_path, capture_time)
                entry.update(details)
                entry["errors"].extend(errors)
                if errors:
                    entry["parse_status"] = "error"
            else:
                # Binary/document extraction is outside this deterministic pass;
                # the manifest path and evidence kind are still checked.
                file_observations = []
            observations.extend(file_observations)
            entry["observation_count"] = len(file_observations)
        except Exception as error:
            entry["parse_status"] = "error"
            entry["errors"].append(f"{type(error).__name__}: {error}")
        registry[file_id] = entry

    csv_observations, csv_registry = _csv_observations()
    observations.extend(csv_observations)
    registry.update(csv_registry)

    registry_file = Path(registry_path)
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    failures = {
        file_id: entry["errors"]
        for file_id, entry in registry.items()
        if entry["parse_status"] != "ok"
    }
    if failures:
        raise RuntimeError(f"Evidence build failed for {len(failures)} source(s): {failures}")
    return observations, registry


def main() -> int:
    try:
        observations, registry = extract_all()
    except Exception as error:
        print(f"deterministic extraction failed: {error}", file=sys.stderr)
        return 1
    print(f"observations: {len(observations)}")
    print(f"registry entries: {len(registry)}")
    print(f"registry: {REGISTRY_PATH}")
    print("parse status: all ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
