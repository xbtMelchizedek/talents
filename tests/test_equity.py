import copy
import json
from pathlib import Path

import pytest

from talents import equity as eq

VEC = json.loads((Path(__file__).resolve().parents[1] / "vectors" / "equity-v1.json").read_text())
E = eq.ONE


def ser(rows):
    return {"v": eq.V_SERIES, "account": "0xabc", "venue": "hl-testnet",
            "samples": [{"l1_block": 1 + i, "ts_ms": 1000 + i, "equity_e8": e, "flow_e8": f}
                        for i, (e, f) in enumerate(rows)]}


@pytest.mark.parametrize("case", VEC["cases"], ids=[c["name"] for c in VEC["cases"]])
def test_golden_vectors(case):
    assert eq.stats(case["series"]) == case["stats"]


def test_deposit_and_withdrawal_do_not_move_the_index():
    assert eq.stats(ser([(100 * E, 0), (200 * E, 100 * E)]))["index_e8"] == E
    assert eq.stats(ser([(100 * E, 0), (40 * E, -60 * E)]))["index_e8"] == E


def test_drawdown_measured_from_running_peak():
    s = eq.stats(ser([(100 * E, 0), (200 * E, 0), (150 * E, 0), (300 * E, 0), (240 * E, 0)]))
    assert s["max_drawdown_bps"] == 2500 and s["total_return_bps"] == 14000


def test_negative_return_truncates_toward_zero():
    # -1.5 bps must report -1, not -2 (Python // would floor to -2)
    s = eq.stats(ser([(10_000 * E, 0), (10_000 * E - 150 * E // 100, 0)]))
    assert s["index_e8"] == E - 15_000 and s["total_return_bps"] == -1


def test_commitment_binds_every_field():
    base = ser([(100 * E, 0), (110 * E, 0)])
    c0 = eq.commitment(base)
    for path in (("account",), ("samples", 1, "equity_e8"), ("samples", 1, "l1_block"), ("samples", 1, "ts_ms")):
        s = copy.deepcopy(base)
        tgt = s
        for k in path[:-1]:
            tgt = tgt[k]
        tgt[path[-1]] = tgt[path[-1]] + (1 if isinstance(tgt[path[-1]], int) else "x")
        assert eq.commitment(s) != c0
    assert eq.stats(base)["series"] == c0


@pytest.mark.parametrize("bad", [1.0, True, "100", None])
def test_refuses_non_int_numbers(bad):
    s = ser([(100 * E, 0), (110 * E, 0)])
    s["samples"][1]["equity_e8"] = bad
    with pytest.raises(TypeError):
        eq.stats(s)


@pytest.mark.parametrize("mutate", [
    lambda s: s["samples"][1].update(l1_block=s["samples"][0]["l1_block"]),
    lambda s: s["samples"][1].update(ts_ms=s["samples"][0]["ts_ms"] - 1),
    lambda s: s["samples"][0].update(flow_e8=1),
    lambda s: s["samples"][1].update(equity_e8=-1),
    lambda s: s["samples"][1].update(extra=0),
    lambda s: s.update(samples=s["samples"][:1]),
    lambda s: s.update(v="talents.equity/0"),
    lambda s: s.update(extra=1),
])
def test_rejects_malformed_series(mutate):
    s = ser([(100 * E, 0), (110 * E, 0)])
    mutate(s)
    with pytest.raises((ValueError, TypeError)):
        eq.stats(s)


def test_rejects_flow_larger_than_equity_and_growth_from_zero():
    with pytest.raises(ValueError):
        eq.stats(ser([(100 * E, 0), (10 * E, 50 * E)]))
    with pytest.raises(ValueError):
        eq.stats(ser([(0, 0), (5 * E, 0)]))


def test_vectors_regenerate_identically(tmp_path, monkeypatch):
    import runpy
    root = Path(__file__).resolve().parents[1]
    before = (root / "vectors" / "equity-v1.json").read_text()
    runpy.run_path(str(root / "scripts" / "gen_equity_vectors.py"), run_name="__main__")
    assert (root / "vectors" / "equity-v1.json").read_text() == before
