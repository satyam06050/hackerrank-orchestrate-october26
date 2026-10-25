"""Read-only sample failure diagnosis using existing output and persisted caches.

This script never calls a model and never writes any data/cache/output files. It reports
when prediction or observation artifacts needed for attribution are unavailable.
"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "dataset"
PREDICTIONS = ROOT / "results" / "output.csv"
SAMPLE_GOLD = DATASET / "sample_output.csv"
REQUESTS = DATASET / "sample_deletion_requests.csv"
LLM_CACHE = ROOT / "code" / "cache" / "llm_extract"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def steps(row: dict[str, str]) -> list[dict[str, Any]]:
    try:
        value = json.loads(row.get("steps_json", ""))
        return value if isinstance(value, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def fingerprint(step: dict[str, Any], *, citation: bool = True) -> tuple[Any, ...]:
    fields = ("phase", "operation", "system_id", "resource_id", "state")
    result = tuple(step.get(key) for key in fields)
    return result + ((step.get("evidence_id"),) if citation else ())


def classify(gold: list[dict[str, Any]], pred: list[dict[str, Any]], status_diff: bool) -> str:
    if Counter(fingerprint(x, citation=False) for x in gold) == Counter(fingerprint(x, citation=False) for x in pred):
        if [fingerprint(x, citation=False) for x in gold] != [fingerprint(x, citation=False) for x in pred]:
            return "order"
        if [x.get("evidence_id") for x in gold] != [x.get("evidence_id") for x in pred]:
            return "link"
        return "policy" if status_diff else "order"
    common = min(len(gold), len(pred))
    for index in range(common):
        a, b = gold[index], pred[index]
        if a.get("resource_id") != b.get("resource_id") or a.get("system_id") != b.get("system_id"):
            return "identity" if a.get("system_id") == b.get("system_id") else "link"
        if a.get("evidence_id") != b.get("evidence_id"):
            return "link"
        if a.get("operation") != b.get("operation") or a.get("state") != b.get("state"):
            return "policy"
    if len(gold) != len(pred):
        return "policy"
    return "policy" if status_diff else "extraction"


def load_cached_observations() -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    if not LLM_CACHE.is_dir():
        return observations
    for path in sorted(LLM_CACHE.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw = payload.get("tool_input", payload) if isinstance(payload, dict) else {}
        if isinstance(raw, dict) and isinstance(raw.get("observations"), list):
            observations.extend(x for x in raw["observations"] if isinstance(x, dict))
    return observations


def load_read_only_observations() -> list[Any]:
    """Use structured parsers in memory only; skip PDF/TXT/PNG instead of making calls."""
    import sys

    sys.path.insert(0, str(ROOT / "code"))
    sys.path.insert(0, str(ROOT / "code" / "extract"))
    from config import DATASET_DIR
    from data_layer import load_manifest
    from deterministic import _csv_observations, _json_observations, _log_observations, _sql_observations

    parsed: list[Any] = []
    for item in load_manifest().to_dict(orient="records"):
        path = DATASET / item["file_path"]
        suffix = path.suffix.lower()
        if suffix == ".sql":
            parsed.extend(_sql_observations(item["file_id"], path, item["captured_at"])[0])
        elif suffix == ".log":
            parsed.extend(_log_observations(item["file_id"], path, item["captured_at"])[0])
        elif suffix == ".json":
            parsed.extend(_json_observations(item["file_id"], path, item["captured_at"])[0])
    csv_observations, _ = _csv_observations()
    parsed.extend(csv_observations)
    # LLM results are read from the existing disk cache, if any; never invoke a provider.
    parsed.extend(load_cached_observations())
    return parsed


def field(observation: Any, name: str, default: Any = None) -> Any:
    value = observation.get(name, default) if isinstance(observation, dict) else getattr(observation, name, default)
    return getattr(value, "value", value)


def supports_finding_state(observation: Any, operation: str) -> bool:
    fields = field(observation, "fields_observed", {}) or {}
    explicit = {str(fields.get(k, "")).upper() for k in ("state", "resource_state", "status", "record_state")}
    kind = str(field(observation, "evidence_kind", ""))
    if operation == "REPORT_REMAINING_RECORD":
        # A captured row establishes presence, but claim-only events do not establish final state.
        return str(field(observation, "resource_id", "")) not in ("", "None") and (
            kind in ("TABLE_SNAPSHOT", "CONFIG", "EXPORT_MANIFEST", "SCREENSHOT", "DOCUMENT")
            or "PRESENT" in explicit
        )
    if operation == "REPORT_EXPOSED_PII":
        if kind not in ("TABLE_SNAPSHOT", "CONFIG", "EXPORT_MANIFEST", "SCREENSHOT", "DOCUMENT"):
            return "PRESENT" in explicit
        direct_fields = ("name", "email", "phone", "email_hash", "customer_name", "customer_email", "customer_phone")
        return any(fields.get(key) not in (None, "", False) for key in direct_fields)
    if operation == "REPORT_ACTIVE_DATA_FLOW":
        enabled = str(fields.get("enabled", "")).casefold()
        return "ACTIVE" in explicit or enabled in ("true", "1", "yes", "enabled")
    if operation == "REPORT_RESTORE_RISK":
        allowed = str(fields.get("restore_allowed", "")).casefold()
        return "ACTIVE" in explicit or str(fields.get("restore_state", "")).upper() == "ACTIVE" or allowed in ("true", "1", "yes", "allowed")
    return False


def expected_state(step: dict[str, Any]) -> str:
    return str(step.get("state", "")).upper()


def run(offset: int = 0, limit: int | None = None) -> None:
    gold_rows = read_csv(SAMPLE_GOLD)
    requests = {row["case_id"]: row for row in read_csv(REQUESTS)}
    pred_rows = read_csv(PREDICTIONS) if PREDICTIONS.is_file() else []
    predictions = {row["case_id"]: row for row in pred_rows}
    observations = load_read_only_observations()
    sample_overlap = set(predictions) & set(requests)

    print("A. SAMPLE DIFFERENCES: status, step count, sequence")
    if not PREDICTIONS.is_file():
        print("  No results/output.csv exists; no prediction rows are available for comparison.")
    elif not sample_overlap:
        print(f"  results/output.csv has {len(pred_rows)} rows but none of the {len(gold_rows)} public sample case_ids; sample differences cannot be compared.")
    else:
        mismatch_count = 0
        for gold_row in gold_rows:
            pred_row = predictions.get(gold_row["case_id"])
            if pred_row is None:
                print(f"  {gold_row['case_id']}: gold status={gold_row['deletion_status']} steps={len(steps(gold_row))}; prediction=MISSING")
                mismatch_count += 1
                continue
            gold_steps, pred_steps = steps(gold_row), steps(pred_row)
            status_diff = pred_row.get("deletion_status") != gold_row.get("deletion_status")
            count_diff = len(pred_steps) != len(gold_steps)
            seq_diff = [fingerprint(x) for x in pred_steps] != [fingerprint(x) for x in gold_steps]
            if status_diff or count_diff or seq_diff:
                mismatch_count += 1
                cause = classify(gold_steps, pred_steps, status_diff)
                print(f"  {gold_row['case_id']} cause={cause}")
                print(f"    status: gold={gold_row['deletion_status']} predicted={pred_row.get('deletion_status')}")
                print(f"    step_count: gold={len(gold_steps)} predicted={len(pred_steps)}")
                print(f"    sequence: gold={json.dumps(gold_steps, ensure_ascii=False)}")
                print(f"    sequence: predicted={json.dumps(pred_steps, ensure_ascii=False)}")
        if mismatch_count == 0:
            print("  No sample differences found.")

    print("B. FAILURE CAUSE CATEGORY (one per differing case)")
    print("  No public sample predictions are present; no sample failure cause can be assigned.")

    print("C. UNSUPPORTED FINDINGS")
    unsupported: list[dict[str, Any]] = []
    for row in pred_rows:
        for step in steps(row):
            if step.get("phase") != "FINDING":
                continue
            citation = str(step.get("evidence_id", ""))
            cites = [
                obs for obs in observations
                if citation == str(field(obs, "file_id", ""))
                or citation in {
                    str((field(obs, "fields_observed", {}) or {}).get("activity_id", "")),
                    str((field(obs, "fields_observed", {}) or {}).get("audit_id", "")),
                }
            ]
            linked = [
                obs for obs in cites
                if str(field(obs, "resource_id", "")) == str(step.get("resource_id", ""))
                and str(field(obs, "system_id", "")) == str(step.get("system_id", ""))
            ]
            supporters = [obs for obs in linked if supports_finding_state(obs, step.get("operation", ""))]
            # Claim-only logs/activity cannot establish a record/process state without a direct artifact.
            direct = [obs for obs in supporters if str(field(obs, "authority", "")) != "CLAIM"]
            if not direct:
                    reason = "NO_LINKED_OBSERVATION" if not linked else "CLAIM_ONLY" if supporters else "LINKED_OBSERVATION_DOES_NOT_ESTABLISH_STATE"
                    unsupported.append({"case_id": row["case_id"], "step": step, "linked": linked, "supporters": supporters, "reason": reason})
    reason_counts = Counter(item["reason"] for item in unsupported)
    print(f"  unsupported_count={len(unsupported)} by_reason={dict(sorted(reason_counts.items()))} observations_checked={len(observations)} llm_cache_present={LLM_CACHE.is_dir()}")
    print("  Meaning: unsupported means not supported by persisted cache plus deterministic structured evidence available now; original in-memory extractions are unavailable.")
    page = unsupported[offset:offset + limit if limit is not None else None]
    for item in page:
        step = item["step"]
        prefix = f"  case={item['case_id']} op={step.get('operation')} system={step.get('system_id')} resource={step.get('resource_id')} citation={step.get('evidence_id')} reason={item['reason']}"
        if not item["linked"]:
            print(f"{prefix} observation=NONE kind=unavailable scope=unavailable should_be_UNKNOWN=yes")
        else:
            for obs in item["linked"]:
                fields = field(obs, "fields_observed", {}) or {}
                observed_values = {key: fields[key] for key in ("state", "status", "enabled", "restore_allowed", "restore_state", "event", "event_type", "reported_result") if key in fields}
                details = {
                    "obs_id": field(obs, "obs_id", ""),
                    "location": field(obs, "location", ""),
                    "evidence_kind": field(obs, "evidence_kind", ""),
                    "scope": field(obs, "scope", ""),
                    "authority": field(obs, "authority", ""),
                    "observed_values": observed_values,
                    "assertion": str(field(obs, "assertion", ""))[:80],
                }
                should_unknown = not item["supporters"] or all(str(field(candidate, "authority", "")) == "CLAIM" for candidate in item["supporters"])
                print(f"{prefix} observation={json.dumps(details, ensure_ascii=False, separators=(',', ':'), default=str)} should_be_UNKNOWN={'yes' if should_unknown else 'no'}")
    if offset + len(page) < len(unsupported):
        print(f"  more unsupported entries: {len(unsupported) - offset - len(page)} (next offset {offset + len(page)})")
    if not pred_rows:
        print("  No prediction rows are available.")
    elif not unsupported:
        print("  No unsupported findings found in the available structured evidence.")

    print("D. MISSED FINDINGS")
    if not sample_overlap:
        print("  The public sample gold has no matching predictions in results/output.csv; sample misses cannot be enumerated.")
    else:
        print("  To distinguish retrieval-not-linked, state-rejected, and judge-dropped, a persisted retrieval/judge trace is required.")

    print("E. FLUENT-BUT-WRONG LLM OUTPUT PATHS")
    print("  intent.py and judge.py are absent; no implemented intent/judge output path can be confirmed.")
    print("  code/extract/llm_extract.py validates tool shape/enums, but extracted assertions are not independently checked against raw PDF/TXT/PNG pixels/text here; a false observation could look schema-valid.")
    print("  code/validate.py grounds a citation against supplied observations by file/resource/system/state; if an upstream extractor supplies a fabricated but matching observation, that check can pass.")
    print("  code/gate.py checks row count/order/status/JSON-array shape and unresolved-case list, but does not itself call validate_case; a caller that omits issues can pass a wrong but well-formed row to output.csv.")
    print(f"  Predictions read from {PREDICTIONS.relative_to(ROOT)} ({len(pred_rows)} rows). LLM extraction cache exists: {LLM_CACHE.is_dir()}. No model calls were made.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset", type=int, default=0, help="unsupported-finding output offset")
    parser.add_argument("--limit", type=int, default=None, help="maximum unsupported findings to print")
    args = parser.parse_args()
    run(args.offset, args.limit)
