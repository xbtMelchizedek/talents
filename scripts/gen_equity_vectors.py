#!/usr/bin/env python3
"""Regenerate vectors/equity-v1.json. Synthetic series only (no real account data)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from talents import equity as eq  # noqa: E402

ACCT = "0x00000000000000000000000000000000000000a1"  # placeholder, not a real account
E = eq.ONE


def series(rows):
    return {"v": eq.V_SERIES, "account": ACCT, "venue": "hl-testnet",
            "samples": [{"l1_block": 1000 + 10 * i, "ts_ms": 1_795_000_000_000 + 60_000 * i, "equity_e8": e,
                         "flow_e8": f} for i, (e, f) in enumerate(rows)]}


cases = {
    "flat": [(100 * E, 0), (100 * E, 0)],
    "gain-then-drawdown": [(100 * E, 0), (120 * E, 0), (90 * E, 0), (99 * E, 0)],
    "deposit-is-not-return": [(100 * E, 0), (150 * E, 50 * E), (165 * E, 0)],
    "withdrawal-is-not-loss": [(100 * E, 0), (60 * E, -50 * E), (66 * E, 0)],
    "small-loss-truncates-toward-zero": [(3 * E, 0), (3 * E - 1, 0)],
    "zero-then-funded": [(0, 0), (10 * E, 10 * E), (11 * E, 0)],
    "liquidated": [(10 * E, 0), (0, 0)],
}
out = {"v": "talents.equity-vectors/1",
       "cases": [{"name": n, "series": series(r), "stats": eq.stats(series(r))} for n, r in cases.items()]}
(ROOT / "vectors" / "equity-v1.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
print(f"wrote {len(out['cases'])} cases")
