import unittest

from phase_dispatch import bind_verification_evidence


class VerificationIdentityTests(unittest.TestCase):
    def test_missing_identity_is_bound_without_changing_assertions_or_source(self):
        source = {"type": "VERIFICATION_RECORDED", "assertions": {"acceptance_coverage": False}}
        bound = bind_verification_evidence(source, "33")
        self.assertEqual(bound["ticket_id"], "33")
        self.assertEqual(bound["assertions"], source["assertions"])
        self.assertNotIn("ticket_id", source)

    def test_existing_identity_is_preserved(self):
        source = {"type": "VERIFICATION_RECORDED", "ticket_id": "33"}
        self.assertEqual(bind_verification_evidence(source, "33"), source)

    def test_conflicting_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            bind_verification_evidence({"type": "VERIFICATION_RECORDED", "ticket_id": "34"}, "33")

    def test_validation_only_identity_is_bound(self):
        self.assertEqual(bind_verification_evidence({"type": "VALIDATION_ONLY_RECORDED"}, "33")["ticket_id"], "33")

    def test_final_is_not_assigned_ticket_identity(self):
        source = {"type": "FINAL_VERIFICATION_RECORDED"}
        self.assertEqual(bind_verification_evidence(source, None), source)
        with self.assertRaises(ValueError):
            bind_verification_evidence(source, "33")

    def test_incomplete_acceptance_is_never_promoted(self):
        source = {"type": "VERIFICATION_RECORDED", "baseline_red": False,
                  "acceptance_coverage_complete": False, "environment_parity": False}
        result = bind_verification_evidence(source, "33", {"base_commit": "base"}, "tests")
        self.assertEqual(result["baseline_red_base"], "base")
        self.assertEqual(result["independent_test_commit"], "tests")
        self.assertEqual(result["environment_status"], "incomplete")
        self.assertEqual(result["acceptance_coverage_status"], "incomplete")
        self.assertFalse(result["baseline_red"])

    def test_conflicting_base_or_test_commit_is_rejected(self):
        for key in ("baseline_red_base", "independent_test_commit"):
            with self.assertRaises(ValueError):
                bind_verification_evidence({"type": "VERIFICATION_RECORDED", key: "wrong"},
                                           "33", {"base_commit": "base"}, "tests")


if __name__ == "__main__":
    unittest.main()
