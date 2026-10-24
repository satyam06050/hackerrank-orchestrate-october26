"""Print the generated source registry and SQL table/scope summary."""

import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent / "source_registry.json"


def main() -> int:
    if not REGISTRY_PATH.is_file():
        raise FileNotFoundError(f"source registry does not exist: {REGISTRY_PATH}")
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    print("SOURCE REGISTRY")
    print(json.dumps(registry, ensure_ascii=False, indent=2))
    print("\nSQL SCOPE DECISIONS")
    for file_id, entry in registry.items():
        if entry.get("kind") != "TABLE_SNAPSHOT" or not file_id.startswith("evidence_"):
            continue
        if not entry.get("tables_covered"):
            continue
        counts = entry.get("table_row_counts", {})
        tables = ", ".join(f"{name}={counts.get(name, 0)}" for name in entry["tables_covered"])
        print(f"{file_id}: scope={entry.get('scope')} tables=[{tables}]")
        print(f"  reason: {entry.get('scope_decision_reason', '')}")
    print("\nZERO-OBSERVATION FILES")
    zero_files = [
        (file_id, entry)
        for file_id, entry in registry.items()
        if entry.get("observation_count") == 0
    ]
    if not zero_files:
        print("none")
    for file_id, entry in zero_files:
        print(
            f"{file_id}: path={entry.get('path')} kind={entry.get('kind')} "
            f"parse_status={entry.get('parse_status')} errors={entry.get('errors')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
