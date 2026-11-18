"""Hyperliquid TESTNET adapter: read-only info calls, order wire building and a pre-trade check.

Milestone 1 never signs or submits. The base URL is pinned to testnet; there is no mainnet switch.
Wire format follows the official docs and the official Python SDK's order wire
(`{"a","b","p","s","r","t"}`, action `{"type":"order","orders":[...],"grouping":"na"}` plus optional
`"builder": {"b": address, "f": tenths of a bp}`).
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from .mandate import DayState, Mandate, Verdict, check_hl_order
from .units import from_e8, to_e8

TESTNET_INFO = "https://api.hyperliquid-testnet.xyz/info"
TESTNET_EVM_RPC = "https://rpc.hyperliquid-testnet.xyz/evm"
TESTNET_CHAIN_ID = 998
TIFS = ("Alo", "Gtc", "Ioc")
MAX_BUILDER_FEE_PERP_TENTHS_BP = 100  # 0.1% (docs: builder codes)
MAX_BUILDER_FEE_SPOT_TENTHS_BP = 1000  # 1%
_ADDR = re.compile(r"^0x[0-9a-f]{40}$")


def info(body: dict[str, Any], url: str = TESTNET_INFO, timeout: int = 20) -> Any:
    if url != TESTNET_INFO:
        raise ValueError("milestone 1 talks to Hyperliquid testnet only")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def perp_universe(meta_and_ctxs: Any) -> dict[str, dict[str, Any]]:
    """coin -> {asset, szDecimals, maxLeverage, markPx, oraclePx} from a metaAndAssetCtxs response."""
    meta, ctxs = meta_and_ctxs
    out = {}
    for i, (u, c) in enumerate(zip(meta["universe"], ctxs)):
        out[u["name"]] = {"asset": i, "szDecimals": u["szDecimals"], "maxLeverage": u["maxLeverage"],
                          "markPx": c.get("markPx"), "oraclePx": c.get("oraclePx")}
    return out


def order_wire(asset: int, is_buy: bool, px: str, sz: str, reduce_only: bool, tif: str, cloid: str | None = None) -> dict:
    if tif not in TIFS:
        raise ValueError(f"tif must be one of {TIFS}")
    w = {"a": asset, "b": is_buy, "p": from_e8(to_e8(px)), "s": from_e8(to_e8(sz)), "r": reduce_only,
         "t": {"limit": {"tif": tif}}}
    if cloid is not None:
        if not re.fullmatch(r"0x[0-9a-f]{32}", cloid):
            raise ValueError("cloid must be 0x + 32 lowercase hex")
        w["c"] = cloid
    return w


def order_action(wires: list[dict], builder: str | None = None, fee_tenths_bp: int = 0, spot: bool = False) -> dict:
    a: dict[str, Any] = {"type": "order", "orders": wires, "grouping": "na"}
    if builder is not None:
        cap = MAX_BUILDER_FEE_SPOT_TENTHS_BP if spot else MAX_BUILDER_FEE_PERP_TENTHS_BP
        if not _ADDR.fullmatch(builder):
            raise ValueError("builder must be a lowercase 0x address")
        if not 0 <= fee_tenths_bp <= cap:
            raise ValueError(f"builder fee must be 0..{cap} tenths of a bp")
        a["builder"] = {"b": builder, "f": fee_tenths_bp}
    return a


def size_ok(sz: str, sz_decimals: int) -> bool:
    return to_e8(sz) % (10 ** (8 - sz_decimals)) == 0


def price_ok(px: str, sz_decimals: int, spot: bool = False) -> bool:
    """Docs (tick and lot size): integer prices always pass; else <= 5 significant figures and
    <= (6 for perps, 8 for spot) - szDecimals decimal places."""
    from decimal import Decimal
    d = Decimal(from_e8(to_e8(px)))
    if d <= 0:
        return False
    if d == d.to_integral_value():
        return True
    t = d.normalize().as_tuple()
    decimals = -t.exponent
    sig = len(t.digits)
    return sig <= 5 and decimals <= (8 if spot else 6) - sz_decimals


def preflight(m: Mandate, day: DayState, uni: dict[str, dict], *, coin: str, is_buy: bool, px: str, sz: str,
              reduce_only: bool, leverage: int, tif: str, now_ms: int, builder: str | None = None,
              fee_tenths_bp: int = 0) -> tuple[Verdict, dict | None]:
    """Mandate check against the live testnet mark, then the unsigned action. Returns (verdict, action)."""
    if coin not in uni:
        return Verdict(False, [f"{coin} is not a testnet perp"]), None
    u = uni[coin]
    v = check_hl_order(m, day, coin=coin, is_buy=is_buy, px=px, sz=sz, reduce_only=reduce_only,
                       leverage=leverage, mark_px=str(u["markPx"]), now_ms=now_ms)
    if leverage > int(u["maxLeverage"]):
        v.deny(f"{coin} max leverage on HL is {u['maxLeverage']}x")
    if not size_ok(sz, int(u["szDecimals"])):
        v.deny(f"size {sz} has more than szDecimals={u['szDecimals']} decimals")
    if not price_ok(px, int(u["szDecimals"])):
        v.deny(f"price {px} breaks HL tick rules (5 significant figures, {6 - int(u['szDecimals'])} decimals max)")
    if not v.ok:
        return v, None
    return v, order_action([order_wire(u["asset"], is_buy, px, sz, reduce_only, tif)], builder, fee_tenths_bp)
