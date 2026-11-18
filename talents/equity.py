"""Deterministic integer stats over an account-equity series (the PnL-anchoring input, design §2.5).

The plan (milestone 3): a HyperEVM checkpoint contract stores (L1 block, equity) samples read from
Hyperliquid's account precompiles; an SP1 program recomputes the stats below over that series and the
proof verifies on HyperEVM or natively on FLOP (Proof Court, SP1 v5 shrink proofs). This module is the
reference implementation and the golden vectors that the Rust program must match byte for byte.
Nothing here reads a chain, signs or sends anything.

Model
  * A sample is ``{"l1_block": int, "ts_ms": int, "equity_e8": int, "flow_e8": int}``. ``flow_e8`` is the
    net external flow (deposits positive, withdrawals negative) that landed after the previous sample
    and is already included in ``equity_e8``. Deposits are not returns.
  * Time-weighted growth index, 1e8 = 1.0. For each period
    ``index_i = index_{i-1} * (equity_i - flow_i) // equity_{i-1}``. All three factors are >= 0, so
    floor division equals truncation toward zero (same result as Rust u128 division).
  * ``total_return_bps`` = (index_n - 1e8) * 10_000 / 1e8, truncated toward zero.
  * ``max_drawdown_bps`` = max over i of (peak_index - index_i) * 10_000 // peak_index.
  * If ``equity_{i-1}`` is 0 the period has no defined return: allowed only when nothing but flows
    happened (``equity_i == flow_i``); the index carries over. Otherwise the series is rejected.

Every number is an int; floats and bools are refused so two implementations cannot disagree on parsing.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

V_SERIES = "talents.equity/1"
DOMAIN = b"talents/1|equity|"
ONE = 10**8
BPS = 10_000
SAMPLE_FIELDS = ("l1_block", "ts_ms", "equity_e8", "flow_e8")
MAX_SAMPLES = 10_000
MAX_E8 = 2**96  # far above any real account; keeps index * equity inside u128 in the Rust port


def _int(v: Any, name: str) -> int:
    if type(v) is not int:  # rejects bool, float, str
        raise TypeError(f"{name} must be an int, got {type(v).__name__}")
    return v


def _trunc_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def check_series(series: dict[str, Any]) -> list[dict[str, int]]:
    if set(series) != {"v", "account", "venue", "samples"}:
        raise ValueError("series needs exactly v, account, venue, samples")
    if series["v"] != V_SERIES:
        raise ValueError(f"unknown series version {series['v']!r}")
    if not isinstance(series["account"], str) or not isinstance(series["venue"], str):
        raise TypeError("account and venue must be strings")
    samples = series["samples"]
    if not isinstance(samples, list) or not 2 <= len(samples) <= MAX_SAMPLES:
        raise ValueError(f"need 2..{MAX_SAMPLES} samples")
    out: list[dict[str, int]] = []
    for i, s in enumerate(samples):
        if not isinstance(s, dict) or set(s) != set(SAMPLE_FIELDS):
            raise ValueError(f"sample {i} needs exactly {SAMPLE_FIELDS}")
        row = {k: _int(s[k], f"samples[{i}].{k}") for k in SAMPLE_FIELDS}
        if not 0 <= row["equity_e8"] <= MAX_E8 or abs(row["flow_e8"]) > MAX_E8:
            raise ValueError(f"sample {i}: equity or flow out of range")
        if i == 0 and row["flow_e8"] != 0:
            raise ValueError("first sample is the baseline; its flow_e8 must be 0")
        if out and (row["l1_block"] <= out[-1]["l1_block"] or row["ts_ms"] <= out[-1]["ts_ms"]):
            raise ValueError(f"sample {i}: l1_block and ts_ms must strictly increase")
        out.append(row)
    return out


def canonical(d: dict[str, Any]) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def commitment(series: dict[str, Any]) -> str:
    """The public input an SP1 proof would bind to: sha256 over the canonical, validated series."""
    check_series(series)
    return "sha256:" + hashlib.sha256(DOMAIN + canonical(series)).hexdigest()


def stats(series: dict[str, Any]) -> dict[str, Any]:
    rows = check_series(series)
    index = peak = ONE
    max_dd = up = down = 0
    for prev, cur in zip(rows, rows[1:]):
        base, pre_flow = prev["equity_e8"], cur["equity_e8"] - cur["flow_e8"]
        if pre_flow < 0:
            raise ValueError(f"block {cur['l1_block']}: equity minus flow is negative (flow larger than equity)")
        if base == 0:
            if pre_flow != 0:
                raise ValueError(f"block {cur['l1_block']}: return from zero equity is undefined")
            continue
        new = index * pre_flow // base
        up += new > index
        down += new < index
        index = new
        peak = max(peak, index)
        max_dd = max(max_dd, (peak - index) * BPS // peak if peak else 0)
    return {
        "v": "talents.equity-stats/1",
        "series": commitment(series),
        "periods": len(rows) - 1,
        "up_periods": up,
        "down_periods": down,
        "first_block": rows[0]["l1_block"],
        "last_block": rows[-1]["l1_block"],
        "net_flow_e8": sum(r["flow_e8"] for r in rows),
        "end_equity_e8": rows[-1]["equity_e8"],
        "index_e8": index,
        "total_return_bps": _trunc_div((index - ONE) * BPS, ONE),
        "max_drawdown_bps": max_dd,
    }
