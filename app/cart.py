"""A tiny shopping cart, used to demo Greenprint.

There is a deliberate off-by-one bug in `cart_total` below. Ask Claude Code to
fix it and watch Greenprint force a failing test (RED) before the edit, then
refuse to finish until that test passes (GREEN).
"""


def cart_total(prices):
    """Return the sum of every item price in the cart.

    prices: a list of numbers (item prices).

    >>> cart_total([10, 20, 30])
    60
    """
    total = 0
    for i in range(len(prices) - 1):  # BUG: off-by-one, silently drops the last item
        total += prices[i]
    return total


def apply_coupon(total, percent_off):
    """Return `total` reduced by `percent_off` percent."""
    return total * (1 - percent_off / 100.0)
