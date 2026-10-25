"""Read-only sample failure diagnosis using existing output and persisted caches.

This script never calls a model and never writes any data/cache/output files. It reports
when prediction or observation artifacts needed for attribution are unavailable.
"""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset"
PREDICTIONS = ROOT / "output.csv"
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


def expected_state(step: dict[str, Any]) -> str:
    return str(step.get("state", "")).upper()


def run() -> None:
    gold_rows = read_csv(SAMPLE_GOLD)
    requests = {row["case_id"]: row for row in read_csv(REQUESTS)}
    pred_rows = read_csv(PREDICTIONS) if PREDICTIONS.is_file() else []
    predictions = {row["case_id"]: row for row in pred_rows}
    observations = load_cached_observations()
    obs_by_file: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        obs_by_file.setdefault(str(obs.get("file_id", "")), []).append(obs)

    print("A. SAMPLE DIFFERENCES: status, step count, sequence")
    if not PREDICTIONS.is_file():
        print("  No output.csv exists; no prediction rows are available for comparison.")
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
    print("  No cached predictions are available; no case-level cause can be assigned.")

    print("C. UNSUPPORTED FINDINGS")
    if not pred_rows:
        print("  No predicted findings are available to inspect.")
    elif not observations:
        print("  Prediction rows exist, but no LLM observation cache exists; observations, evidence_kind/scope, and UNKNOWN adequacy cannot be determined.")
    else:
        unsupported = []
        for row in pred_rows:
            for step in steps(row):
                if step.get("phase") != "FINDING":
                    continue
                cites = obs_by_file.get(str(step.get("evidence_id", "")), [])
                matching = [o for o in cites if o.get("resource_id") == step.get("resource_id") and o.get("system_id") == step.get("system_id")]
                if not matching:
                    unsupported.append((row["case_id"], step))
                else:
                    for obs in matching:
                        fields = obs.get("fields_observed", {}) or {}
                        state = str(fields.get("state", fields.get("status", ""))).upper()
                        if state and state != expected_state(step):
                            unsupported.append((row["case_id"], step, obs))
        if not unsupported:
            print("  No cached observation conflicts found. This is not proof of support outside cached artifacts.")
        for item in unsupported:
            case_id, step = item[0], item[1]
            print(f"  case={case_id} finding={json.dumps(step, ensure_ascii=False)}")
            if len(item) == 2:
                print("    producing observation: none found in cache; evidence_kind/scope=unavailable; should_be_UNKNOWN=yes")
            else:
                obs = item[2]
                print(f"    observation={json.dumps(obs, ensure_ascii=False)} evidence_kind={obs.get('evidence_kind')} scope={obs.get('scope')} should_be_UNKNOWN=no (explicit cached state conflicts)")

    print("D. MISSED FINDINGS")
    if not pred_rows:
        print("  No predicted plans are available; misses cannot be enumerated.")
    else:
        print("  To distinguish retrieval-not-linked, state-rejected, and judge-dropped, a persisted retrieval/judge trace is required.")

    print("E. FLUENT-BUT-WRONG LLM OUTPUT PATHS")
    print("  intent.py and judge.py are absent; no implemented intent/judge output path can be confirmed.")
    print("  code/extract/llm_extract.py validates tool shape/enums, but extracted assertions are not independently checked against raw PDF/TXT/PNG pixels/text here; a false observation could look schema-valid.")
    print("  code/validate.py grounds a citation against supplied observations by file/resource/system/state; if an upstream extractor supplies a fabricated but matching observation, that check can pass.")
    print("  code/gate.py checks row count/order/status/JSON-array shape and unresolved-case list, but does not itself call validate_case; a caller that omits issues can pass a wrong but well-formed row to output.csv.")
    print("  No root output.csv, LLM extraction cache, or usage/cache trace was present during this run. No model calls were made.")


if __name__ == "__main__":
    run()
