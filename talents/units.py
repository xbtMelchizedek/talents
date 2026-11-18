"""Fixed-point helpers. Every money number inside talents is an int (1e8 scale), never a float."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

SCALE = 10**8


def to_e8(x: str | int | Decimal) -> int:
    """'64000.5' -> 6400050000000. Refuses floats and anything that would round."""
    if isinstance(x, float):
        raise TypeError("pass prices and sizes as strings or Decimals, not floats")
    try:
        d = Decimal(str(x))
    except InvalidOperation as e:
        raise ValueError(f"not a number: {x!r}") from e
    q = d * SCALE
    if q != q.to_integral_value():
        raise ValueError(f"{x} has more than 8 decimals")
    return int(q)


def from_e8(n: int) -> str:
    """6400050000000 -> '64000.5' (HL wire style: no trailing zeros)."""
    d = (Decimal(n) / SCALE).normalize()
    s = f"{d:f}"
    return "0" if s in ("-0", "") else s


def notional_e8(px_e8: int, sz_e8: int) -> int:
    return px_e8 * sz_e8 // SCALE
