"""Tests for the AI plan parser: rule-based path, LLM path, and AI fallbacks."""

import unittest

from splitmate.config import Config
from splitmate.fake_services import FakeServices
from splitmate.plan import PlanValidationError
from splitmate.parser import parse_plan

from tests.helpers import demo_text


def rules_config(**overrides):
    env = {}
    env.update(overrides)
    return Config.from_env(env=env)


class RuleParserDemoTests(unittest.TestCase):
    def setUp(self):
        self.result = parse_plan(demo_text(), rules_config())

    def test_reports_rule_parser(self):
        self.assertEqual(self.result["parser"], "rules")
        self.assertIsNone(self.result["model"])

    def test_title_and_currency(self):
        self.assertEqual(self.result["plan"]["title"], "Cabin weekend")
        self.assertEqual(self.result["plan"]["currency"], "USD")

    def test_finds_all_participants_in_order(self):
        self.assertEqual(
            self.result["plan"]["participants"], ["Alex", "Sam", "Priya", "Jordan"]
        )

    def test_finds_every_expense_with_amounts(self):
        expenses = {expense["description"]: expense for expense in self.result["plan"]["expenses"]}
        self.assertEqual(
            set(expenses), {"Cabin", "Groceries", "Gas", "Firewood", "Wine"}
        )
        self.assertEqual(expenses["Cabin"]["amount_cents"], 62000)
        self.assertEqual(expenses["Groceries"]["amount_cents"], 18000)
        self.assertEqual(expenses["Gas"]["amount_cents"], 9500)
        self.assertEqual(expenses["Firewood"]["amount_cents"], 4000)
        self.assertEqual(expenses["Wine"]["amount_cents"], 9000)

    def test_finds_payers(self):
        expenses = {expense["description"]: expense for expense in self.result["plan"]["expenses"]}
        self.assertEqual(expenses["Cabin"]["paid_by"], "Alex")
        self.assertEqual(expenses["Groceries"]["paid_by"], "Priya")
        self.assertEqual(expenses["Gas"]["paid_by"], "Priya")
        self.assertEqual(expenses["Firewood"]["paid_by"], "Jordan")
        self.assertEqual(expenses["Wine"]["paid_by"], "Alex")

    def test_partial_attendance_sam_only_cabin_and_gas(self):
        expenses = {expense["description"]: expense for expense in self.result["plan"]["expenses"]}
        self.assertIn("Sam", expenses["Cabin"]["shared_by"])
        self.assertIn("Sam", expenses["Gas"]["shared_by"])
        self.assertNotIn("Sam", expenses["Groceries"]["shared_by"])
        self.assertNotIn("Sam", expenses["Firewood"]["shared_by"])
        self.assertNotIn("Sam", expenses["Wine"]["shared_by"])

    def test_wine_split_between_subset(self):
        expenses = {expense["description"]: expense for expense in self.result["plan"]["expenses"]}
        self.assertEqual(expenses["Wine"]["shared_by"], ["Alex", "Priya"])

    def test_jordan_skipped_wine(self):
        expenses = {expense["description"]: expense for expense in self.result["plan"]["expenses"]}
        self.assertNotIn("Jordan", expenses["Wine"]["shared_by"])

    def test_no_warnings_and_ready_for_orders(self):
        self.assertEqual(self.result["warnings"], [])
        self.assertTrue(self.result["ready"])

    def test_split_preview_matches_expected_orders(self):
        orders = {order["name"]: order["amount_cents"] for order in self.result["split"]["orders"]}
        self.assertEqual(orders, {"Sam": 17875, "Priya": 2208, "Jordan": 21208})

    def test_notes_capture_adjustments(self):
        notes = " ".join(self.result["plan"]["notes"])
        self.assertIn("Sam left Saturday morning", notes)
        self.assertIn("Jordan skipped", notes)

    def test_deterministic_output(self):
        again = parse_plan(demo_text(), rules_config())
        self.assertEqual(again["plan"], self.result["plan"])


class RuleParserVariantTests(unittest.TestCase):
    def test_payer_forms(self):
        result = parse_plan(
            "Participants: Ana, Ben. The cabin was paid by Ana for $620. "
            "Ben covered $90 for groceries.",
            rules_config(),
        )
        expenses = {expense["description"]: expense for expense in result["plan"]["expenses"]}
        self.assertEqual(expenses["Cabin"]["paid_by"], "Ana")
        self.assertEqual(expenses["Groceries"]["paid_by"], "Ben")
        self.assertEqual(expenses["Cabin"]["amount_cents"], 62000)

    def test_currency_detection_euro(self):
        result = parse_plan("Participants: Ana, Ben. Dinner \u20ac100 paid by Ana.", rules_config())
        self.assertEqual(result["plan"]["currency"], "EUR")
        self.assertEqual(result["plan"]["expenses"][0]["amount_cents"], 10000)

    def test_multi_item_sentence_shares_payer(self):
        result = parse_plan(
            "Participants: Ana, Ben. Cabin $300 and food $90 paid by Ana.", rules_config()
        )
        payers = [expense["paid_by"] for expense in result["plan"]["expenses"]]
        self.assertEqual(payers, ["Ana", "Ana"])

    def test_inferred_participants_warns(self):
        result = parse_plan("Cabin $100 paid by Ana. Food $40 paid by Ben.", rules_config())
        self.assertEqual(result["plan"]["participants"], ["Ana", "Ben"])
        self.assertTrue(any("inferred" in warning.lower() for warning in result["warnings"]))

    def test_missing_payer_marks_not_ready(self):
        result = parse_plan(
            "Participants: Ana, Ben. Cabin $100. Food $40 paid by Ben.", rules_config()
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("no payer" in warning for warning in result["warnings"]))

    def test_no_expenses_raises_validation_error(self):
        with self.assertRaises(PlanValidationError):
            parse_plan("Just a trip with Ana and Ben.", rules_config())

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            parse_plan("   ", rules_config())

    def test_overlong_text_rejected(self):
        with self.assertRaises(ValueError):
            parse_plan("x" * 9000, rules_config())

    def test_amount_formats(self):
        result = parse_plan(
            "Participants: Ana, Ben. Cabin $1,234.56 paid by Ana. Gas 95.5 paid by Ben.",
            rules_config(),
        )
        amounts = [expense["amount_cents"] for expense in result["plan"]["expenses"]]
        self.assertEqual(amounts, [123456, 9550])

    def test_skipped_item_exclusion(self):
        result = parse_plan(
            "Participants: Ana, Ben, Cy. Cabin $300 paid by Ana. Food $90 paid by Ben. "
            "Cy skipped food.",
            rules_config(),
        )
        food = next(expense for expense in result["plan"]["expenses"] if expense["description"] == "Food")
        self.assertEqual(food["shared_by"], ["Ana", "Ben"])


class LLMParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeServices().start()

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def setUp(self):
        self.fake.reset()

    def _llm_config(self, **overrides):
        env = {
            "LLM_API_KEY": "fake-key",
            "LLM_BASE_URL": self.fake.llm_base_url,
            "LLM_MODEL": "fake-model",
            "SPLITMATE_PUBLIC_BASE_URL": "http://127.0.0.1:8000",
        }
        env.update(overrides)
        return Config.from_env(env=env)

    def test_llm_plan_is_used_and_validated(self):
        result = parse_plan("ignored by the fake: cabin trip", self._llm_config())
        self.assertEqual(result["parser"], "llm")
        self.assertEqual(result["model"], "fake-model")
        self.assertEqual(result["plan"]["participants"], ["Alex", "Sam", "Priya", "Jordan"])
        self.assertEqual(len(result["plan"]["expenses"]), 5)
        self.assertEqual(self.fake.stats()["llm_requests"], 1)

    def test_llm_plan_preview_orders(self):
        result = parse_plan("fake llm", self._llm_config())
        orders = {order["name"]: order["amount_cents"] for order in result["split"]["orders"]}
        self.assertEqual(orders, {"Sam": 17875, "Priya": 2208, "Jordan": 21208})

    def test_llm_http_failure_falls_back_to_rules(self):
        result = parse_plan(demo_text(), self._llm_config(LLM_MODEL="fail-model"))
        self.assertEqual(result["parser"], "rules")
        self.assertTrue(any("AI parsing was unavailable" in warning for warning in result["warnings"]))

    def test_llm_bad_json_falls_back_to_rules(self):
        result = parse_plan(demo_text(), self._llm_config(LLM_MODEL="bad-json-model"))
        self.assertEqual(result["parser"], "rules")
        self.assertTrue(any("AI parsing was unavailable" in warning for warning in result["warnings"]))

    def test_llm_incomplete_plan_falls_back_to_rules(self):
        result = parse_plan(demo_text(), self._llm_config(LLM_MODEL="invalid-plan-model"))
        self.assertEqual(result["parser"], "rules")
        self.assertEqual(len(result["plan"]["participants"]), 4)

    def test_secrets_never_in_warning_text(self):
        result = parse_plan(demo_text(), self._llm_config(LLM_MODEL="fail-model"))
        self.assertNotIn("fake-key", " ".join(result["warnings"]))


class ReminderTests(unittest.TestCase):
    def test_mock_reminder_mentions_name_amount_and_event(self):
        from splitmate.reminders import draft_reminder

        config = rules_config()
        plan = {"title": "Cabin weekend", "currency": "USD"}
        share = {"name": "Sam", "amount_cents": 17875, "status": "order_created", "approve_url": "https://example.test/x"}
        result = draft_reminder(plan, share, config)
        self.assertEqual(result["mode"], "mock")
        self.assertIn("Sam", result["message"])
        self.assertIn("$178.75", result["message"])
        self.assertIn("Cabin weekend", result["message"])
        self.assertIn("https://example.test/x", result["message"])


class LLMReminderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeServices().start()

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def test_llm_reminder_uses_model(self):
        from splitmate.reminders import draft_reminder

        config = Config.from_env(
            env={
                "LLM_API_KEY": "fake-key",
                "LLM_BASE_URL": self.fake.llm_base_url,
                "LLM_MODEL": "fake-model",
            }
        )
        result = draft_reminder(
            {"title": "Cabin weekend", "currency": "USD"},
            {"name": "Sam", "amount_cents": 17875, "status": "order_created", "approve_url": ""},
            config,
        )
        self.assertEqual(result["mode"], "llm")
        self.assertIn("Sam", result["message"])


if __name__ == "__main__":
    unittest.main()
