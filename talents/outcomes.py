"""Hyperliquid HIP-4 outcome markets (prediction markets) on TESTNET: metadata, asset ids, preflight.

Docs (verified 2026-11-18): outcome asset encoding = 10 * outcome + side (side 0 or 1); spot coin
`#<encoding>`, token `+<encoding>`, asset id `100_000_000 + encoding`. Outcomes are fully collateralized
and settle in [0, 1] quote per share; trading fees are currently zero; builder fees apply to sell orders.
Price rule used here (inferred: outcomes "share most implementation details with spot"): 0 < px < 1 and at
most 5 significant figures. Never signs or submits.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from . import hl
from .mandate import DayState, Mandate, Verdict, check_outcome_order
from .units import from_e8, to_e8

ASSET_BASE = 100_000_000


def encoding(outcome: int, side: int) -> int:
    if side not in (0, 1):
        raise ValueError("side must be 0 or 1")
    if outcome < 0:
        raise ValueError("outcome id must be >= 0")
    return 10 * outcome + side


def asset_id(outcome: int, side: int) -> int:
    return ASSET_BASE + encoding(outcome, side)


def coin(outcome: int, side: int) -> str:
    return f"#{encoding(outcome, side)}"


def parse_description(desc: str) -> dict[str, str]:
    """'feed:...|threshold:100000|time:20261001-1600' -> {'feed': ..., 'threshold': '100000', ...}"""
    out = {}
    for part in desc.split("|"):
        k, sep, v = part.partition(":")
        if sep:
            out[k] = v
    return out


def index(outcome_meta: dict[str, Any]) -> dict[int, dict[str, Any]]:
    idx = {}
    for o in outcome_meta.get("outcomes", []):
        idx[int(o["outcome"])] = {"name": o["name"], "fields": parse_description(o.get("description", "")),
                                  "sides": [s["name"] for s in o.get("sideSpecs", [])], "quote": o.get("quoteToken"),
                                  "venue": o.get("venue")}
    return idx


def price_ok(px: str) -> bool:
    d = Decimal(from_e8(to_e8(px)))
    if not 0 < d < 1:
        return False
    return len(d.normalize().as_tuple().digits) <= 5


def preflight(m: Mandate, day: DayState, idx: dict[int, dict], *, outcome: int, side: int, is_buy: bool, px: str,
              sz: str, tif: str, now_ms: int, builder: str | None = None, fee_tenths_bp: int = 0
              ) -> tuple[Verdict, dict | None]:
    if outcome not in idx:
        return Verdict(False, [f"outcome {outcome} not in testnet outcomeMeta"]), None
    v = check_outcome_order(m, day, outcome=outcome, side=side, px=px, sz=sz, now_ms=now_ms)
    if not price_ok(px):
        v.deny("outcome price must be in (0, 1) with at most 5 significant figures")
    if builder is not None and is_buy:
        v.reasons.append("note: builder fees on outcomes apply to sell orders only")
    if not v.ok:
        return v, None
    wire = hl.order_wire(asset_id(outcome, side), is_buy, px, sz, False, tif)
    return v, hl.order_action([wire], builder, fee_tenths_bp, spot=True)
