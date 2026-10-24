"""Contract tests for the canonical schema module; uses only the standard library."""

import unittest

import schema


class SchemaContractTests(unittest.TestCase):
    def test_allowed_triples_match_contract_table(self):
        expected = {
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
        self.assertEqual(schema.ALLOWED, expected)

    def test_operation_order_contains_each_contract_operation_once(self):
        ordered = [
            operation
            for phase in schema.PHASES
            for operation in schema.OPERATION_ORDER[phase]
        ]
        contract_operations = [operation for _, operation, _ in schema.ALLOWED]
        self.assertEqual(len(ordered), len(set(ordered)))
        self.assertCountEqual(ordered, contract_operations)
        self.assertEqual(set(schema.OPERATION_ORDER), set(schema.PHASES))

    def test_judge_tool_has_only_item_link_rule_and_citation(self):
        function = schema.JUDGE_TOOL_SCHEMA["function"]
        self.assertEqual(
            set(function["parameters"]["properties"]),
            {"item", "link", "rule_id", "citation"},
        )
        self.assertEqual(
            function["parameters"]["properties"]["link"]["enum"],
            ["yes", "no", "unsure"],
        )
        self.assertTrue(function["strict"])
        self.assertEqual(
            schema.JUDGE_TOOL_CHOICE,
            {"type": "function", "function": {"name": function["name"]}},
        )
        self.assertNotIn("status", function["parameters"]["properties"])
        self.assertNotIn("steps", function["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
