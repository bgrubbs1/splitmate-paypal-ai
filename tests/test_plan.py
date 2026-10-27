"""Unit tests for money helpers, plan validation and normalisation."""

import unittest

from splitmate.plan import (
    MAX_AMOUNT_CENTS,
    PlanValidationError,
    cents_to_value,
    money,
    normalize_plan,
    parse_money_to_cents,
    validate_plan,
)


class MoneyParsingTests(unittest.TestCase):
    def test_parse_plain_integer_dollars(self):
        self.assertEqual(parse_money_to_cents("620"), 62000)
        self.assertEqual(parse_money_to_cents(620), 62000)

    def test_parse_symbol_and_commas(self):
        self.assertEqual(parse_money_to_cents("$1,234.56"), 123456)
        self.assertEqual(parse_money_to_cents("$95"), 9500)

    def test_parse_partial_decimal(self):
        self.assertEqual(parse_money_to_cents("95.5"), 9550)
        self.assertEqual(parse_money_to_cents("0.07"), 7)

    def test_parse_float_input(self):
        self.assertEqual(parse_money_to_cents(1234.5), 123450)

    def test_parse_currency_suffix(self):
        self.assertEqual(parse_money_to_cents("USD 95"), 9500)
        self.assertEqual(parse_money_to_cents("90 dollars"), 9000)

    def test_rejects_garbage_and_negative(self):
        self.assertIsNone(parse_money_to_cents("not money"))
        self.assertIsNone(parse_money_to_cents("-5"))
        self.assertIsNone(parse_money_to_cents(None))
        self.assertIsNone(parse_money_to_cents(True))

    def test_cents_to_value(self):
        self.assertEqual(cents_to_value(0), "0.00")
        self.assertEqual(cents_to_value(1), "0.01")
        self.assertEqual(cents_to_value(15500), "155.00")
        self.assertEqual(cents_to_value(123456), "1234.56")

    def test_money_formatting(self):
        self.assertEqual(money(15500, "USD"), "$155.00")
        self.assertEqual(money(123456, "USD"), "$1,234.56")
        self.assertEqual(money(0, "USD"), "$0.00")
        self.assertEqual(money(15500, "EUR"), "\u20ac155.00")
        self.assertEqual(money(15500, "GBP"), "\u00a3155.00")


class PlanValidationTests(unittest.TestCase):
    def _valid(self, **overrides):
        plan = {
            "title": "Trip",
            "currency": "USD",
            "participants": ["Ana", "Ben"],
            "expenses": [
                {
                    "description": "Cabin",
                    "amount_cents": 20000,
                    "paid_by": "Ana",
                    "shared_by": ["Ana", "Ben"],
                }
            ],
            "notes": [],
        }
        plan.update(overrides)
        return plan

    def test_valid_plan_has_no_errors(self):
        self.assertEqual(validate_plan(self._valid()), [])

    def test_non_object(self):
        self.assertEqual(validate_plan("nope"), ["Plan must be a JSON object."])

    def test_needs_two_participants(self):
        errors = validate_plan(self._valid(participants=["Ana"]))
        self.assertTrue(any("at least 2 participants" in error for error in errors))

    def test_duplicate_participants(self):
        errors = validate_plan(self._valid(participants=["Ana", "ana"]))
        self.assertTrue(any("listed twice" in error for error in errors))

    def test_duplicate_expense_amounts(self):
        expense = {"description": "Cabin", "amount_cents": 0, "paid_by": "Ana"}
        errors = validate_plan(self._valid(expenses=[expense]))
        self.assertTrue(any("positive amount" in error for error in errors))

    def test_amount_above_limit(self):
        expense = {
            "description": "Yacht",
            "amount_cents": MAX_AMOUNT_CENTS + 1,
            "paid_by": "Ana",
        }
        errors = validate_plan(self._valid(expenses=[expense]))
        self.assertTrue(any("limit" in error for error in errors))

    def test_payer_must_be_participant(self):
        expense = {
            "description": "Cabin",
            "amount_cents": 100,
            "paid_by": "Stranger",
        }
        errors = validate_plan(self._valid(expenses=[expense]))
        self.assertTrue(any("not a participant" in error for error in errors))

    def test_shared_by_unknown_name(self):
        expense = {
            "description": "Cabin",
            "amount_cents": 100,
            "paid_by": "Ana",
            "shared_by": ["Ana", "Ghost"],
        }
        errors = validate_plan(self._valid(expenses=[expense]))
        self.assertTrue(any("not a participant" in error for error in errors))

    def test_shared_by_must_not_be_empty(self):
        expense = {
            "description": "Cabin",
            "amount_cents": 100,
            "paid_by": "Ana",
            "shared_by": [],
        }
        errors = validate_plan(self._valid(expenses=[expense]))
        self.assertTrue(any("non-empty list" in error for error in errors))

    def test_missing_payer_allowed_for_preview(self):
        expense = {"description": "Cabin", "amount_cents": 100}
        self.assertTrue(validate_plan(self._valid(expenses=[expense])))
        self.assertEqual(
            validate_plan(self._valid(expenses=[expense]), allow_missing_payers=True), []
        )

    def test_unknown_currency(self):
        errors = validate_plan(self._valid(currency="XYZ"))
        self.assertTrue(any("currency must be one of" in error for error in errors))


class PlanNormalisationTests(unittest.TestCase):
    def test_defaults_shared_by_to_all_participants(self):
        plan = normalize_plan(
            {
                "participants": ["Ana", "Ben"],
                "expenses": [{"description": "Cabin", "amount": "100", "paid_by": "ana"}],
            }
        )
        expense = plan["expenses"][0]
        self.assertEqual(expense["amount_cents"], 10000)
        self.assertEqual(expense["shared_by"], ["Ana", "Ben"])
        self.assertEqual(expense["paid_by"], "Ana")  # canonical spelling

    def test_defaults_title_and_currency(self):
        plan = normalize_plan(
            {
                "participants": ["Ana", "Ben"],
                "expenses": [{"description": "Gas", "amount": "10", "paid_by": "Ben"}],
            }
        )
        self.assertEqual(plan["title"], "Group expenses")
        self.assertEqual(plan["currency"], "USD")
        self.assertEqual(plan["source"], "manual")

    def test_control_characters_stripped(self):
        plan = normalize_plan(
            {
                "title": "Trip\x00\x07",
                "participants": ["Ana\x00", "Ben"],
                "expenses": [{"description": "Gas", "amount": "10", "paid_by": "Ben"}],
            }
        )
        self.assertNotIn("\x00", plan["title"])
        self.assertNotIn("\x00", plan["participants"][0])

    def test_raises_validation_error(self):
        with self.assertRaises(PlanValidationError) as context:
            normalize_plan({"participants": ["Ana"], "expenses": []})
        self.assertTrue(context.exception.errors)
        self.assertIn(";", str(context.exception))


if __name__ == "__main__":
    unittest.main()
