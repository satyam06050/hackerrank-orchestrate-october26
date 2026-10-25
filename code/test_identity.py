"""Standard-library tests for identity resolution and cutoff reconstruction."""

import hashlib
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import IdentitySet, Unresolved, _resolve_frames  # noqa: E402
from data_layer import index_audit_log, index_identities  # noqa: E402


IDENTITY_COLUMNS = ["user_id", "name", "email", "phone", "email_hash", "updated_at"]
AUDIT_COLUMNS = ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"]


def frames():
    identities = pd.DataFrame(
        [
            ["u1", "One", "new@example.com", "111", hashlib.sha256(b"new@example.com").hexdigest(), "2025-03-01T00:00:00Z"],
            ["u2", "Two", "other@example.com", "222", hashlib.sha256(b"other@example.com").hexdigest(), "2025-01-01T00:00:00Z"],
        ], columns=IDENTITY_COLUMNS,
    ).astype(str)
    audit = pd.DataFrame(
        [
            ["a1", "u1", "2025-02-01T00:00:00Z", "email", "old@example.com", "new@example.com"],
            ["a2", "u1", "2025-02-01T00:00:00Z", "email_hash", hashlib.sha256(b"old@example.com").hexdigest(), hashlib.sha256(b"new@example.com").hexdigest()],
            ["a3", "u1", "2025-02-10T00:00:00Z", "phone", "111", "333"],
            ["a4", "u1", "2025-02-20T00:00:00Z", "email", "new@example.com", "future@example.com"],
        ], columns=AUDIT_COLUMNS,
    ).astype(str)
    # Current snapshot reflects all events, including future@example.com and phone 333.
    identities.loc[identities.user_id == "u1", "email"] = "future@example.com"
    identities.loc[identities.user_id == "u1", "email_hash"] = hashlib.sha256(b"future@example.com").hexdigest()
    identities.loc[identities.user_id == "u1", "phone"] = "333"
    return identities, audit


class IdentityTests(unittest.TestCase):
    def test_historical_email_resolves_to_right_user(self):
        identities, audit = frames()
        result = _resolve_frames("OLD@example.com", "2025-02-15T00:00:00Z", identities, audit)
        self.assertIsInstance(result, IdentitySet)
        self.assertEqual(result.user_id, "u1")
        self.assertTrue(result.resolved_via_history)

    def test_identifier_acquired_after_cutoff_is_excluded(self):
        identities, audit = frames()
        result = _resolve_frames("old@example.com", "2025-01-31T23:59:59Z", identities, audit)
        values = {item.value for item in result.identifiers}
        self.assertNotIn("future@example.com", values)
        self.assertNotIn("333", values)
        self.assertNotIn("new@example.com", values)
        self.assertIn("111", values)

    def test_email_matching_two_users_is_unresolved(self):
        identities, audit = frames()
        audit.loc[len(audit)] = ["a5", "u2", "2025-02-01T00:00:00Z", "email", "old@example.com", "other@example.com"]
        result = _resolve_frames("old@example.com", "2025-03-01T00:00:00Z", identities, audit)
        self.assertEqual(result, Unresolved("multiple_matches", ("u1", "u2")))

    def test_email_matching_nobody_is_unresolved(self):
        identities, audit = frames()
        self.assertEqual(_resolve_frames("nobody@example.com", "2025-03-01T00:00:00Z", identities, audit), Unresolved("zero_matches"))

    def test_as_of_matches_current_snapshot_when_no_later_changes(self):
        identities, audit = frames()
        result = _resolve_frames("future@example.com", "2025-03-01T00:00:00Z", identities, audit)
        self.assertIsInstance(result, IdentitySet)
        fields = {(item.field, item.value) for item in result.identifiers}
        current = identities.loc[identities.user_id == "u1"].iloc[0]
        self.assertIn(("email", current.email), fields)
        self.assertIn(("phone", current.phone), fields)
        self.assertIn(("email_hash", current.email_hash), fields)

    def test_hash_scheme_reproduces_current_snapshot(self):
        identities = index_identities()
        audit = index_audit_log()
        self.assertTrue(identities["by_user_id"])
        self.assertTrue(audit["by_user_id"])
        from data_layer import _LOADED_FRAMES
        frame = _LOADED_FRAMES["identities"]
        reproduced = sum(
            hashlib.sha256(row.email.encode("utf-8")).hexdigest() == row.email_hash
            for row in frame.itertuples(index=False)
        )
        self.assertEqual(reproduced, len(frame))


if __name__ == "__main__":
    unittest.main(verbosity=2)
