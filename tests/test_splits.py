"""Unit tests for the fair-split math (rounding, partial attendance, netting)."""

import unittest

from splitmate.plan import normalize_plan
from splitmate.splits import compute_split, split_amount


def plan_from(expenses, participants=("Alex", "Sam", "Priya", "Jordan"), **overrides):
    raw = {
        "title": "Test trip",
        "currency": "USD",
        "participants": list(participants),
        "expenses": [
            {
                "description": expense[0],
                "amount_cents": expense[1],
                "paid_by": expense[2],
                "shared_by": list(expense[3]) if len(expense) > 3 else list(participants),
            }
            for expense in expenses
        ],
    }
    raw.update(overrides)
    return normalize_plan(raw)


class SplitAmountTests(unittest.TestCase):
    def test_even_division(self):
        self.assertEqual(
            split_amount(62000, ["Alex", "Sam", "Priya", "Jordan"]),
            {"Alex": 15500, "Sam": 15500, "Priya": 15500, "Jordan": 15500},
        )

    def test_largest_remainder_rounding(self):
        shares = split_amount(100, ["A", "B", "C"])
        self.assertEqual(shares, {"A": 34, "B": 33, "C": 33})
        self.assertEqual(sum(shares.values()), 100)

    def test_single_cent_split(self):
        shares = split_amount(1, ["A", "B"])
        self.assertEqual(shares, {"A": 1, "B": 0})
        self.assertEqual(sum(shares.values()), 1)

    def test_large_amount_sums_exactly(self):
        participants = ["P{}".format(index) for index in range(7)]
        shares = split_amount(999999, participants)
        self.assertEqual(sum(shares.values()), 999999)
        self.assertLessEqual(max(shares.values()) - min(shares.values()), 1)

    def test_rejects_empty_participants(self):
        with self.assertRaises(ValueError):
            split_amount(100, [])

    def test_rejects_negative_amount(self):
        with self.assertRaises(ValueError):
            split_amount(-1, ["A"])

    def test_rejects_non_integer(self):
        with self.assertRaises(TypeError):
            split_amount(10.5, ["A"])

    def test_is_deterministic(self):
        first = split_amount(1000, ["A", "B", "C"])
        second = split_amount(1000, ["A", "B", "C"])
        self.assertEqual(first, second)


class ComputeSplitDemoTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan_from(
            [
                ("Cabin", 62000, "Alex"),
                ("Groceries", 18000, "Priya", ["Alex", "Priya", "Jordan"]),
                ("Gas", 9500, "Priya"),
                ("Firewood", 4000, "Jordan", ["Alex", "Priya", "Jordan"]),
                ("Wine", 9000, "Alex", ["Alex", "Priya"]),
            ]
        )
        self.split = compute_split(self.plan)

    def test_total_matches_expenses(self):
        self.assertEqual(self.split["total_cents"], 102500)

    def test_every_expense_allocates_exactly_its_amount(self):
        for expense in self.split["expenses"]:
            self.assertEqual(sum(expense["shares"].values()), expense["amount_cents"])
            self.assertEqual(set(expense["shares"]), set(expense["shared_by"]))

    def test_partial_attendance_excludes_sam_from_groceries(self):
        groceries = next(item for item in self.split["expenses"] if item["description"] == "Groceries")
        self.assertNotIn("Sam", groceries["shares"])
        self.assertEqual(groceries["shares"], {"Alex": 6000, "Priya": 6000, "Jordan": 6000})

    def test_wine_only_for_alex_and_priya(self):
        wine = next(item for item in self.split["expenses"] if item["description"] == "Wine")
        self.assertEqual(wine["shares"], {"Alex": 4500, "Priya": 4500})

    def test_firewood_rounding(self):
        firewood = next(item for item in self.split["expenses"] if item["description"] == "Firewood")
        self.assertEqual(firewood["shares"], {"Alex": 1334, "Priya": 1333, "Jordan": 1333})
        self.assertTrue(self.split["rounding_notes"])

    def test_payer_credits(self):
        by_name = {row["name"]: row for row in self.split["participants"]}
        self.assertEqual(by_name["Alex"]["paid_cents"], 71000)
        self.assertEqual(by_name["Priya"]["paid_cents"], 27500)
        self.assertEqual(by_name["Jordan"]["paid_cents"], 4000)
        self.assertEqual(by_name["Sam"]["paid_cents"], 0)

    def test_net_positions(self):
        by_name = {row["name"]: row for row in self.split["participants"]}
        self.assertEqual(by_name["Alex"]["net_cents"], 71000 - 29709)
        self.assertEqual(by_name["Sam"]["net_cents"], -17875)
        self.assertEqual(by_name["Priya"]["net_cents"], 27500 - 29708)
        self.assertEqual(by_name["Jordan"]["net_cents"], 4000 - 25208)

    def test_net_positions_sum_to_zero(self):
        self.assertEqual(sum(row["net_cents"] for row in self.split["participants"]), 0)

    def test_only_net_debtors_get_orders(self):
        debtor_names = {row["name"] for row in self.split["participants"] if row["net_cents"] < 0}
        self.assertEqual({order["name"] for order in self.split["orders"]}, debtor_names)
        self.assertEqual(len(self.split["orders"]), 3)

    def test_order_amounts_equal_debts(self):
        orders = {order["name"]: order["amount_cents"] for order in self.split["orders"]}
        self.assertEqual(orders, {"Sam": 17875, "Priya": 2208, "Jordan": 21208})

    def test_money_is_balanced(self):
        totals = self.split["totals"]
        self.assertTrue(totals["balanced"])
        self.assertEqual(totals["orders_cents"], totals["credits_cents"])
        self.assertEqual(totals["orders_cents"], 41291)

    def test_reasoning_covers_every_expense_and_person(self):
        self.assertGreaterEqual(len(self.split["reasoning"]), 5 + 4)
        self.assertTrue(any("Cabin" in line for line in self.split["reasoning"]))
        self.assertTrue(any("Alex fronted" in line for line in self.split["reasoning"]))

    def test_debtors_and_creditors_are_consistent(self):
        self.assertEqual(
            [item["name"] for item in self.split["debtors"]],
            [order["name"] for order in self.split["orders"]],
        )
        self.assertEqual(self.split["creditors"], [{"name": "Alex", "amount_cents": 41291}])


class ComputeSplitEdgeTests(unittest.TestCase):
    def test_two_person_one_expense(self):
        split = compute_split(plan_from([("Cabin", 10000, "Alex")], participants=("Alex", "Sam")))
        by_name = {row["name"]: row for row in split["participants"]}
        self.assertEqual(by_name["Alex"]["net_cents"], 5000)
        self.assertEqual(by_name["Sam"]["net_cents"], -5000)
        self.assertEqual(split["orders"], [{"name": "Sam", "amount_cents": 5000}])

    def test_payer_who_does_not_share_gets_full_credit(self):
        split = compute_split(
            plan_from([("Gift", 9000, "Alex", ["Sam", "Priya"])], participants=("Alex", "Sam", "Priya"))
        )
        by_name = {row["name"]: row for row in split["participants"]}
        self.assertEqual(by_name["Alex"]["net_cents"], 9000)
        self.assertEqual(by_name["Sam"]["net_cents"], -4500)
        self.assertEqual(by_name["Priya"]["net_cents"], -4500)

    def test_everyone_settled(self):
        plan = normalize_plan(
            {
                "title": "Even",
                "participants": ["A", "B"],
                "expenses": [
                    {"description": "Taxi", "amount_cents": 2000, "paid_by": "A"},
                    {"description": "Snacks", "amount_cents": 2000, "paid_by": "B"},
                ],
            }
        )
        split = compute_split(plan)
        self.assertEqual(split["orders"], [])
        self.assertEqual(split["debtors"], [])
        self.assertEqual(split["creditors"], [])
        self.assertTrue(all(row["status"] == "settled" for row in split["participants"]))

    def test_deterministic(self):
        plan = plan_from([("Cabin", 12345, "Alex"), ("Food", 6789, "Priya")])
        self.assertEqual(compute_split(plan), compute_split(plan))


if __name__ == "__main__":
    unittest.main()
