"""Validate participant dataset counts, manifest paths, and loader stability."""

from data_layer import (
    frames_checksum,
    index_activity,
    index_audit_log,
    index_identities,
    load_manifest,
    load_requests,
    load_rules,
    load_sample_output,
    load_sample_requests,
    resolve_evidence_path,
)


def _load_everything():
    requests = load_requests()
    samples = load_sample_requests()
    identities = index_identities()
    audit_log = index_audit_log()
    activity = index_activity()
    rules = load_rules()
    manifest = load_manifest()
    sample_output = load_sample_output()
    return requests, samples, identities, audit_log, activity, rules, manifest, sample_output


def main() -> None:
    requests, samples, identities, audit_log, activity, rules, manifest, sample_output = _load_everything()

    counts = {
        "requests": len(requests),
        "samples": len(samples),
        "identities": sum(len(rows) for rows in identities["by_user_id"].values()),
        "audit rows": sum(len(rows) for rows in audit_log["by_user_id"].values()),
        "activity rows": sum(len(rows) for rows in activity["by_resource_id"].values()),
        "rules": len(rules),
        "manifest files": len(manifest),
    }
    expected = {
        "requests": 518,
        "samples": 32,
        "identities": 850,
        "audit rows": 306,
        "activity rows": 4011,
        "rules": 15,
        "manifest files": 94,
    }

    print("DATASET CHECKS")
    for name, value in counts.items():
        print(f"{name}: {value} (expected {expected[name]})")
        assert value == expected[name], f"Unexpected {name}: {value}"

    assert len(sample_output) == 32, f"Unexpected sample output rows: {len(sample_output)}"
    case_ids = requests["case_id"]
    assert not case_ids.duplicated().any(), "Duplicate request case_ids found"
    print("duplicate case_ids: none")

    missing_paths = []
    for file_id in manifest["file_id"]:
        try:
            resolve_evidence_path(file_id)
        except FileNotFoundError:
            missing_paths.append(file_id)
    assert not missing_paths, f"Missing manifest evidence paths for file_ids: {missing_paths}"
    print("manifest paths: all 94 exist")

    print("\nLOADER STABILITY")
    before = frames_checksum()
    print(f"initial checksum: {before}")
    _load_everything()
    after = frames_checksum()
    print(f"checksum after re-calling every loader: {after}")
    assert before == after, "Frame checksum changed after re-calling loaders"
    print("loader stability: PASS")

    requests_frame = load_requests()
    original_value = requests_frame.at[0, "case_id"]
    baseline = frames_checksum()
    requests_frame.at[0, "case_id"] = original_value + "__mutated__"
    mutated = frames_checksum()
    assert mutated != baseline, "Mutating one cell did not change the checksum"
    print("single-cell mutation changes checksum: PASS")
    requests_frame.at[0, "case_id"] = original_value
    assert frames_checksum() == baseline, "Restoring the cell did not restore the checksum"
    print("restoring mutated cell restores checksum: PASS")
    print("all dataset checks: PASS")


if __name__ == "__main__":
    main()
