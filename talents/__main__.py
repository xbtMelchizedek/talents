"""python -m talents preflight ...   (read-only Hyperliquid testnet call; never signs, never submits)"""
from __future__ import annotations

import argparse
import json
import time

from . import hl
from .mandate import DayState, Mandate


def main() -> int:
    p = argparse.ArgumentParser(prog="talents")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("preflight", help="check an HL testnet perp order against a mandate and print the unsigned action")
    f.add_argument("--mandate", required=True)
    f.add_argument("--coin", required=True)
    side = f.add_mutually_exclusive_group(required=True)
    side.add_argument("--buy", action="store_true")
    side.add_argument("--sell", action="store_true")
    f.add_argument("--px", required=True)
    f.add_argument("--sz", required=True)
    f.add_argument("--lev", type=int, default=1)
    f.add_argument("--tif", default="Gtc", choices=hl.TIFS)
    f.add_argument("--reduce-only", action="store_true")
    f.add_argument("--used-today-usd", default="0")
    f.add_argument("--builder")
    f.add_argument("--builder-fee", type=int, default=0, help="tenths of a basis point (10 = 1 bp)")
    a = p.parse_args()
    m = Mandate.load(a.mandate)
    from .units import to_e8
    uni = hl.perp_universe(hl.info({"type": "metaAndAssetCtxs"}))
    v, action = hl.preflight(m, DayState(to_e8(a.used_today_usd)), uni, coin=a.coin, is_buy=a.buy, px=a.px, sz=a.sz,
                             reduce_only=a.reduce_only, leverage=a.lev, tif=a.tif, now_ms=int(time.time() * 1000),
                             builder=a.builder, fee_tenths_bp=a.builder_fee)
    print(json.dumps({"ok": v.ok, "reasons": v.reasons, "notional_usd_e8": v.notional_e8,
                      "mark": uni.get(a.coin, {}).get("markPx"), "unsigned_action": action,
                      "note": "unsigned; milestone 1 never signs or submits"}, indent=1))
    return 0 if v.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
