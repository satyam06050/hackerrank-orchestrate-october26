"""CSV loading, indexing, evidence path resolution, and loaded-frame checksums."""

import csv
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from config import DATASET_DIR


_LOADED_FRAMES: dict[str, pd.DataFrame] = {}


def _load_frame(frame_name: str, filename: str) -> pd.DataFrame:
    """Read and register a dataset CSV without pandas NA coercion."""
    path = Path(DATASET_DIR) / filename
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    _LOADED_FRAMES[frame_name] = frame
    return frame


def load_requests() -> pd.DataFrame:
    return _load_frame("requests", "deletion_requests.csv")


def load_sample_requests() -> pd.DataFrame:
    return _load_frame("sample_requests", "sample_deletion_requests.csv")


def load_sample_output() -> pd.DataFrame:
    return _load_frame("sample_output", "sample_output.csv")


def _records_by(frame: pd.DataFrame, column: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for record in frame.to_dict(orient="records"):
        result.setdefault(record[column], []).append(record)
    return result


def index_identities(frame: pd.DataFrame | None = None) -> dict[str, dict[str, Any]]:
    identities = frame if frame is not None else _load_frame("identities", "user_identities.csv")
    _LOADED_FRAMES["identities"] = identities
    records = identities.to_dict(orient="records")
    return {
        "by_user_id": _records_by(identities, "user_id"),
        "by_email": _records_by(identities, "email"),
    }


def index_audit_log(frame: pd.DataFrame | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
    audit = frame if frame is not None else _load_frame("audit_log", "user_audit_log.csv")
    _LOADED_FRAMES["audit_log"] = audit
    return {
        "by_user_id": _records_by(audit, "user_id"),
        "by_old_value": _records_by(audit[audit["old_value"] != ""], "old_value"),
        "by_new_value": _records_by(audit[audit["new_value"] != ""], "new_value"),
    }


def index_activity(frame: pd.DataFrame | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
    activity = frame if frame is not None else _load_frame("activity", "system_activity.csv")
    _LOADED_FRAMES["activity"] = activity
    return {
        "by_resource_id": _records_by(activity, "resource_id"),
        "by_event_type": _records_by(activity, "event_type"),
        "by_source_system": _records_by(activity, "source_system"),
        "by_target_system": _records_by(activity, "target_system"),
    }


def load_rules() -> pd.DataFrame:
    return _load_frame("rules", "deletion_rules.csv")


def load_manifest() -> pd.DataFrame:
    return _load_frame("manifest", "evidence_files.csv")


def resolve_evidence_path(file_id: str) -> Path:
    manifest = load_manifest()
    matching = manifest.loc[manifest["file_id"] == file_id]
    if matching.empty:
        raise FileNotFoundError(f"Evidence file_id {file_id!r} was not found in the manifest")

    path = Path(DATASET_DIR) / matching.iloc[0]["file_path"]
    if not path.is_file():
        raise FileNotFoundError(f"Evidence file_id {file_id!r} is missing at {path}")
    return path.resolve()


def frames_checksum() -> str:
    """Return a deterministic SHA-256 digest of every currently loaded CSV frame."""
    digest = hashlib.sha256()
    for name in sorted(_LOADED_FRAMES):
        frame = _LOADED_FRAMES[name]
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(
            frame.to_csv(
                index=False,
                lineterminator="\n",
                quoting=csv.QUOTE_MINIMAL,
                na_rep="",
            ).encode("utf-8")
        )
        digest.update(b"\0")
    return digest.hexdigest()
