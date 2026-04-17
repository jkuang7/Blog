from __future__ import annotations

import unittest

from orx.tier_contract import PERSISTENCE_FIELDS, build_stage_contract, flatten_stage_contract


class TierContractTests(unittest.TestCase):
    def test_simple_single_project_intake_downgrades_planning_to_high(self) -> None:
        contract = build_stage_contract(item_count=1, project_count=1, needs_clarification=False)

        planning = contract["stages"][0]
        self.assertEqual(planning["stage"], "planning")
        self.assertEqual(planning["default_reasoning_effort"], "xhigh")
        self.assertEqual(planning["selected_reasoning_effort"], "high")
        self.assertEqual(planning["selection_mode"], "simple_intake_downgrade")

    def test_multi_ticket_intake_keeps_planning_at_xhigh(self) -> None:
        contract = build_stage_contract(
            item_count=2,
            project_count=2,
            needs_clarification=False,
        )

        planning = contract["stages"][0]
        self.assertEqual(planning["selected_reasoning_effort"], "xhigh")
        self.assertEqual(planning["selection_mode"], "default")

    def test_contract_exposes_persistence_fields_and_hil_flag(self) -> None:
        contract = build_stage_contract(
            item_count=1,
            project_count=0,
            needs_clarification=True,
        )

        self.assertEqual(contract["persistence_fields"], list(PERSISTENCE_FIELDS))
        self.assertTrue(contract["requires_hil"])
        self.assertEqual(contract["confidence"], "low")
        self.assertEqual(contract["stages"][1]["selected_reasoning_effort"], "high")
        self.assertEqual(contract["stages"][2]["selected_reasoning_effort"], "medium")

    def test_flatten_stage_contract_returns_durable_record_fields(self) -> None:
        flat = flatten_stage_contract(
            build_stage_contract(item_count=1, project_count=1, needs_clarification=False)
        )

        self.assertEqual(flat["planning_stage"], "planning")
        self.assertEqual(flat["planning_model"], "gpt-5.4")
        self.assertEqual(flat["planning_reasoning_effort"], "high")
        self.assertEqual(flat["execution_reasoning_effort"], "medium")
        self.assertEqual(flat["confidence"], "high")
        self.assertFalse(flat["requires_hil"])

    def test_oversized_single_project_intake_keeps_planning_at_xhigh(self) -> None:
        contract = build_stage_contract(
            item_count=1,
            project_count=1,
            needs_clarification=False,
            oversized=True,
        )

        planning = contract["stages"][0]
        self.assertEqual(planning["selected_reasoning_effort"], "xhigh")
        self.assertEqual(planning["selection_mode"], "oversized_intake")


if __name__ == "__main__":
    unittest.main()
