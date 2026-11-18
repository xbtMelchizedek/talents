import json

import pytest

from conftest import ROOT
from talents import callout as co
from talents import didkey

T0 = 1_795_000_000_000


def key(i):
    return didkey.key_from_seed(bytes([i]) * 32)  # PUBLIC test seeds, never real keys


def call(agent, side="long", entry="100", inv="95", ts=T0, nonce="01"):
    return {"v": co.V_CALL, "agent": agent, "venue": "hl-perp", "market": "BTC", "side": side, "entry_px": entry,
            "invalidation_px": inv, "horizon_ms": 3_600_000, "size_bucket": "s", "ts_ms": ts, "nonce": nonce}


def test_round_trip_and_record():
    k = key(1); a = didkey.did_of_key(k)
    c1, c2 = call(a, nonce="01"), call(a, side="short", entry="100", inv="104", ts=T0 + 10, nonce="02")
    s1, s2 = b"\x01" * 32, b"\x02" * 32
    cm1, cm2 = co.make_commit(k, c1, s1, T0 + 1), co.make_commit(k, c2, s2, T0 + 11)
    rv1, rv2 = co.make_reveal(k, c1, s1, cm1), co.make_reveal(k, c2, s2, cm2)
    ex1 = co.make_exit(k, a, co.cid(c1), "110", T0 + 100)  # +1000 bps
    ex2 = co.make_exit(k, a, co.cid(c2), "103", T0 + 200)  # short, -300 bps
    r = co.score(a, [cm1, cm2], [rv1, rv2], [ex1, ex2], now_ms=T0 + 1000)
    assert r.as_dict() == {"agent": a, "calls": 2, "closed": 2, "wins": 1, "sum_bps": 700, "max_drawdown_bps": 300,
                           "hidden": 0, "rejected": []}


def test_hidden_commit_penalized_and_forged_docs_rejected():
    k, x = key(1), key(2)
    a = didkey.did_of_key(k)
    c = call(a)
    s = b"\x03" * 32
    cm = co.make_commit(k, c, s, T0 + 1)
    hidden = co.make_commit(k, call(a, nonce="09"), b"\x04" * 32, T0 + 2)  # a loser the caller never reveals
    forged_exit = co.make_exit(x, a, co.cid(c), "200", T0 + 50)  # signed by someone else
    ex = co.make_exit(k, a, co.cid(c), "101", T0 + 60)
    r = co.score(a, [cm, hidden], [co.make_reveal(k, c, s, cm)], [forged_exit, ex], now_ms=T0 + 2 + 86_400_001)
    assert r.hidden == 1 and r.closed == 1 and r.sum_bps == 100
    assert r.max_drawdown_bps == 1000


def test_reveal_must_open_commit():
    k = key(1); a = didkey.did_of_key(k)
    c = call(a)
    cm = co.make_commit(k, c, b"\x05" * 32, T0 + 1)
    bad = co.make_reveal(k, call(a, entry="99"), b"\x05" * 32, cm)  # changed the call after committing
    r = co.score(a, [cm], [bad], [], now_ms=T0 + 2)
    assert r.calls == 0 and r.rejected


def test_first_exit_wins():
    k = key(1); a = didkey.did_of_key(k)
    c = call(a); s = b"\x06" * 32
    cm = co.make_commit(k, c, s, T0 + 1)
    e1 = co.make_exit(k, a, co.cid(c), "90", T0 + 10)
    e2 = co.make_exit(k, a, co.cid(c), "150", T0 + 20)  # can't rewrite history
    r = co.score(a, [cm], [co.make_reveal(k, c, s, cm)], [e2, e1], now_ms=T0 + 30)
    assert r.sum_bps == -1000


def test_invalid_calls():
    a = didkey.did_of_key(key(1))
    with pytest.raises(ValueError):
        co.check_call(call(a, inv="105"))  # invalidation on the winning side
    with pytest.raises(ValueError):
        co.check_call({**call(a), "extra": 1})


def test_truncation_toward_zero():
    assert co.ret_bps("long", 3 * 10**8, 2 * 10**8) == -3333
    assert co.ret_bps("short", 3 * 10**8, 2 * 10**8) == 3333


def test_golden_vectors():
    vec = json.loads((ROOT / "vectors" / "callout-v1.json").read_text())
    for case in vec["cases"]:
        r = co.score(case["agent"], case["commits"], case["reveals"], case["exits"], now_ms=case["now_ms"])
        assert r.as_dict() == case["record"], case["name"]
