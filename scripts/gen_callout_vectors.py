#!/usr/bin/env python3
"""Regenerate vectors/callout-v1.json. Uses PUBLIC test seeds (bytes([i])*32); never real keys."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from talents import callout as co, didkey  # noqa: E402

T0 = 1_795_000_000_000


def call(a, side, entry, inv, ts, nonce):
    return {"v": co.V_CALL, "agent": a, "venue": "hl-perp", "market": "BTC", "side": side, "entry_px": entry,
            "invalidation_px": inv, "horizon_ms": 3_600_000, "size_bucket": "s", "ts_ms": ts, "nonce": nonce}


def case(name, seed, legs, hidden=0, now=T0 + 10**9):
    k = didkey.key_from_seed(bytes([seed]) * 32)
    a = didkey.did_of_key(k)
    commits, reveals, exits = [], [], []
    for i, (side, entry, inv, exit_px) in enumerate(legs):
        c = call(a, side, entry, inv, T0 + i * 1000, f"{i:02x}")
        s = bytes([i + 1]) * 32
        cm = co.make_commit(k, c, s, T0 + i * 1000 + 1)
        commits.append(cm)
        reveals.append(co.make_reveal(k, c, s, cm))
        if exit_px is not None:
            exits.append(co.make_exit(k, a, co.cid(c), exit_px, T0 + i * 1000 + 500))
    for j in range(hidden):
        c = call(a, "long", "100", "90", T0 + 900_000 + j, f"h{j}")
        commits.append(co.make_commit(k, c, bytes([200 + j]) * 32, T0 + 900_001 + j))
    rec = co.score(a, commits, reveals, exits, now_ms=now)
    return {"name": name, "agent": a, "now_ms": now, "commits": commits, "reveals": reveals, "exits": exits,
            "record": rec.as_dict()}


cases = [
    case("two-wins-one-loss", 11, [("long", "100", "95", "110"), ("short", "50", "55", "45"), ("long", "100", "90", "93")]),
    case("open-position-not-scored", 12, [("long", "100", "95", None), ("yes", "0.40", "0.30", "0.55")]),
    case("hidden-loser-penalized", 13, [("long", "64000", "62000", "64640")], hidden=2),
    case("rounding-toward-zero", 14, [("long", "3", "2", "2"), ("short", "3", "4", "2")]),
]
out = {"v": "talents.callout-vectors/1", "note": "PUBLIC test keys. score() must reproduce `record` exactly.",
       "cases": cases}
(ROOT / "vectors" / "callout-v1.json").write_text(json.dumps(out, indent=1) + "\n")
print("wrote", len(cases), "cases")
