"""Standalone regressions with minimal original public price/size fixtures."""
from decimal import Decimal, localcontext
from fractions import Fraction
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "guava_h1_exact_math.py"
spec = importlib.util.spec_from_file_location("guava_h1_exact_math", MODULE)
exact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exact)

COMPARISON = {"selected_no_token_id": "direct-no",
              "basket_yes_token_ids": ["other-yes-a", "other-yes-b"]}


def books(ask, first_bid, second_bid):
    def levels(rows):
        return [{"price": price, "size": size} for price, size in rows]
    return {
        "direct-no": {"status": "OK", "raw": {"asks": levels(ask)}},
        "other-yes-a": {"status": "OK", "raw": {"bids": levels(first_bid)}},
        "other-yes-b": {"status": "OK", "raw": {"bids": levels(second_bid)}},
    }


def legacy_rounded_gap(evidence):
    """Reproduce the old 28-digit Decimal sign, only as a test reference."""
    def walk(rows, amount, buy):
        remaining, cost, shares = Decimal(amount), Decimal(), Decimal()
        levels = sorted((Decimal(r["price"]), Decimal(r["size"])) for r in rows)
        if not buy:
            levels.reverse()
        for price, size in levels:
            take = min(size, remaining / price if buy else remaining)
            cost += take * price
            shares += take
            remaining -= take * price if buy else take
            if remaining <= Decimal("1e-18"):
                break
        return cost, shares
    with localcontext() as context:
        context.prec = 28
        cost, shares = walk(evidence["direct-no"]["raw"]["asks"], 5, True)
        sales = [walk(evidence[t]["raw"]["bids"], shares, False)[0]
                 for t in COMPARISON["basket_yes_token_ids"]]
        return (sum(sales, Decimal()) - cost) / shares


class ExactH1Tests(unittest.TestCase):
    def test_elche_original_zero_is_not_a_positive_signal(self):
        evidence = books([("0.91", "1024.6")], [("0.88", "6224.02")], [("0.03", "567.33")])
        self.assertEqual(legacy_rounded_gap(evidence), Decimal("1.82e-28"))
        self.assertEqual(exact.exact_gap(COMPARISON, evidence), Fraction(0))

    def test_historical_original_zero_does_not_select_a_false_entry(self):
        evidence = books([("0.83", "2627")], [("0.81", "1506.28")], [("0.02", "1647.15")])
        self.assertEqual(legacy_rounded_gap(evidence), Decimal("1.66e-28"))
        self.assertEqual(exact.exact_gap(COMPARISON, evidence), Fraction(0))

    def test_original_small_real_gap_survives_without_an_epsilon(self):
        evidence = books([("0.979", "4960")], [("0.892", "0.08"), ("0.891", "5.74")], [("0.088", "285.56")])
        self.assertEqual(legacy_rounded_gap(evidence), Decimal("0.000015664"))
        self.assertEqual(exact.exact_gap(COMPARISON, evidence), Fraction(979, 62500000))

    def test_exact_walker_keeps_a_subcent_unfilled_remainder(self):
        result = exact.exact_walk([{"price": "1", "size": "4.9999999999999999999999999999"}], 5)
        self.assertFalse(result["complete"])
        self.assertEqual(result["quantity"], Fraction("4.9999999999999999999999999999"))

    def test_real_price_difference_smaller_than_rounding_noise_is_retained(self):
        buy = exact.exact_walk([{"price": "0.5", "size": "10"}], 5)
        sell = exact.exact_walk([{"price": "0.5000000000000000000000000001", "size": "10"}], 10, buy=False)
        self.assertTrue(buy["complete"] and sell["complete"])
        self.assertEqual(sell["cost"] - buy["cost"], Fraction("1e-27"))

    def test_amount_and_levels_are_explicitly_validated(self):
        for amount in (0, -1, True, "NaN", "Infinity", float("inf"), None, {}):
            with self.subTest(amount_type=type(amount).__name__):
                with self.assertRaises(ValueError):
                    exact.exact_walk([], amount)
        for price, size in (("0", "1"), ("1.01", "1"), ("0.5", "0"),
                            ("0.5", "-1"), ("NaN", "1"), ("0.5", "Infinity"),
                            (True, "1"), ("0.5", False)):
            with self.subTest(price_type=type(price).__name__, size_type=type(size).__name__):
                with self.assertRaises(ValueError):
                    exact.exact_walk([{"price": price, "size": size}], 5)
        for raw in (None, {}, "[]", [None], [{"price": "0.5"}]):
            with self.assertRaises(ValueError):
                exact.exact_walk(raw, 5)
        with self.assertRaises(ValueError):
            exact.exact_walk([], 5, buy=1)

    def test_two_distinct_other_yes_tokens_are_required(self):
        for basket in ([], ["a"], ["a", "b", "c"], ["a", "a"],
                       ["a", "direct-no"], ["a", ""], ["a", None], "ab"):
            with self.subTest(basket_type=type(basket).__name__):
                with self.assertRaises(ValueError):
                    exact.exact_gap({**COMPARISON, "basket_yes_token_ids": basket}, {})

    def test_missing_or_insufficient_depth_has_no_invented_gap(self):
        evidence = books([("0.5", "20")], [("0.25", "20")], [("0.25", "20")])
        self.assertIsNone(exact.exact_gap(COMPARISON, {}))
        evidence["other-yes-b"]["raw"]["bids"] = []
        self.assertIsNone(exact.exact_gap(COMPARISON, evidence))
        evidence["direct-no"]["status"] = "ERROR"
        self.assertIsNone(exact.exact_gap(COMPARISON, evidence))


if __name__ == "__main__":
    unittest.main()
