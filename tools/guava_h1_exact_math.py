"""Exact displayed-book H1 arithmetic, independent of any bot or database.

Prices and sizes are interpreted as their decimal text, then kept as rational
numbers. No epsilon decides whether a gap is positive. This describes a
conditional payoff comparison; it proves neither execution nor settlement
equivalence and does not include fees or select a trading policy.
"""
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from fractions import Fraction

EXACT_SELECTION_VERSION = "guava-h1-exact-rational-v2"
H1_NOTIONAL_USDC = 5


def _fraction(value):
    """Preserve rational intermediates and finite decimal input values."""
    if isinstance(value, bool):
        raise ValueError("boolean is not an economic number")
    if isinstance(value, Fraction):
        return value
    if not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("economic numbers must be finite decimals")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("economic numbers must be finite decimals") from None
    if not decimal.is_finite():
        raise ValueError("economic numbers must be finite decimals")
    return Fraction(decimal)


def exact_walk(raw, amount, buy=True):
    """Walk all supplied levels: BUY amount is cash, SELL amount is shares.

    Empty or insufficient depth returns complete=False and actual consumed
    quantity/cost. Invalid input raises ValueError without echoing its values.
    """
    if type(buy) is not bool:
        raise ValueError("buy must be boolean")
    remaining = _fraction(amount)
    if remaining <= 0:
        raise ValueError("walk amount must be positive")
    if not isinstance(raw, (list, tuple)):
        raise ValueError("book side must be an array of levels")
    levels = []
    for row in raw:
        if not isinstance(row, Mapping) or "price" not in row or "size" not in row:
            raise ValueError("each level requires price and size")
        price, size = _fraction(row["price"]), _fraction(row["size"])
        if not 0 < price <= 1 or size <= 0:
            raise ValueError("level price or size is outside its valid range")
        levels.append((price, size))
    levels.sort(reverse=not buy)
    cost, quantity = Fraction(), Fraction()
    for price, size in levels:
        take = min(size, remaining / price if buy else remaining)
        cost += take * price
        quantity += take
        remaining -= take * price if buy else take
        if remaining == 0:
            break
    return {"complete": remaining == 0, "cost": cost, "quantity": quantity}


def _observed_book(books, token):
    row = books.get(token)
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise ValueError("book evidence must be a mapping")
    if row.get("status") != "OK" or row.get("raw") is None:
        return None
    if not isinstance(row["raw"], Mapping):
        raise ValueError("observed raw book must be a mapping")
    return row["raw"]


def exact_gap(comp, books):
    """Compare a $5 direct NO ask with two other YES bids at equal shares.

    Return the exact gap per share, or None for missing/insufficient evidence.
    Token/condition provenance and payoff comparability belong to the caller.
    """
    if not isinstance(comp, Mapping) or not isinstance(books, Mapping):
        raise ValueError("comparison and books must be mappings")
    no_token = comp.get("selected_no_token_id")
    basket = comp.get("basket_yes_token_ids")
    if (not isinstance(no_token, str) or not no_token.strip()
            or not isinstance(basket, (list, tuple)) or len(basket) != 2
            or any(not isinstance(t, str) or not t.strip() for t in basket)
            or len(set(basket)) != 2 or no_token in basket):
        raise ValueError("H1 requires one NO and two distinct other YES tokens")
    no = _observed_book(books, no_token)
    if no is None:
        return None
    buy = exact_walk(no.get("asks"), H1_NOTIONAL_USDC)
    if not buy["complete"]:
        return None
    proceeds = Fraction()
    for token in basket:
        book = _observed_book(books, token)
        if book is None:
            return None
        sale = exact_walk(book.get("bids"), buy["quantity"], buy=False)
        if not sale["complete"]:
            return None
        proceeds += sale["cost"]
    return (proceeds - buy["cost"]) / buy["quantity"]
