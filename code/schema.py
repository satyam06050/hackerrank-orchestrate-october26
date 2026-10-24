"""Canonical output, evidence, and judging schemas for Ghost Data Still Exists."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


OUTPUT_COLUMNS = ("case_id", "deletion_status", "steps_json")
STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")


ALLOWED = frozenset(
    {
        ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
        ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
        ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
        ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
        ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
        ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
        ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
        ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
        ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
        ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
        ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
        ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
        ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
        ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
    }
)


# Contract table order, not alphabetical order. Dependency overrides remain a
# separate planning concern; this constant records only the listed operation order.
OPERATION_ORDER = {
    "FINDING": [
        "REPORT_REMAINING_RECORD",
        "REPORT_EXPOSED_PII",
        "REPORT_ACTIVE_DATA_FLOW",
        "REPORT_RESTORE_RISK",
    ],
    "REMEDIATION": [
        "DISABLE_DATA_FLOW",
        "BLOCK_RESTORE",
        "DELETE_RECORD",
        "REDACT_RECORD",
        "ANONYMIZE_RECORD",
    ],
    "VERIFICATION": [
        "VERIFY_RECORD_ABSENT",
        "VERIFY_RECORD_PRESENT",
        "VERIFY_RECORD_REDACTED",
        "VERIFY_RECORD_ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED",
        "VERIFY_RESTORE_BLOCKED",
    ],
}


class EvidenceKind(str, Enum):
    TABLE_SNAPSHOT = "TABLE_SNAPSHOT"
    CONFIG = "CONFIG"
    EXPORT_MANIFEST = "EXPORT_MANIFEST"
    EVENT = "EVENT"
    SCREENSHOT = "SCREENSHOT"
    DOCUMENT = "DOCUMENT"
    IDENTITY_CHANGE = "IDENTITY_CHANGE"


class Authority(str, Enum):
    AUTHORITATIVE = "AUTHORITATIVE"
    CORROBORATING = "CORROBORATING"
    CLAIM = "CLAIM"


class Scope(str, Enum):
    FULL_TABLE = "full_table"
    PARTIAL = "partial"
    EVENT = "event"
    VISIBLE_REGION = "visible_region"
    EXPORT_LISTED = "export_listed"


class State(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    REDACTED = "REDACTED"
    ANONYMIZED = "ANONYMIZED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class Observation:
    obs_id: str
    file_id: str
    location: str
    system_id: str
    resource_id: str | None
    evidence_kind: EvidenceKind
    authority: Authority
    scope: Scope
    fields_observed: Mapping[str, Any]
    assertion: str
    observed_at: str
    extraction_method: str


_JUDGE_PARAMETERS = {
    "type": "object",
    "properties": {
        "item": {"type": "string"},
        "link": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "rule_id": {"type": "string"},
        "citation": {"type": "string"},
    },
    "required": ["item", "link", "rule_id", "citation"],
    "additionalProperties": False,
}

JUDGE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "judge_evidence_item",
        "description": "Judge one candidate evidence item link and deletion-rule match.",
        "strict": True,
        "parameters": _JUDGE_PARAMETERS,
    },
}

# Pass this as tool_choice when requesting a judge result; the tool has no
# output-status or final-steps fields by design.
JUDGE_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": "judge_evidence_item"},
}
