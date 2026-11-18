import json
import os

import pytest

from conftest import ROOT
from talents import hl, outcomes
from talents.mandate import DayState, Mandate

NOW = 1_795_000_000_000
META = json.loads((ROOT / "vectors" / "hl_testnet_outcome_meta_sample.json").read_text())
M = Mandate.from_json({"agent": "did:key:zT", "network": "hl-testnet", "venues": ["hl-outcome"], "expires_ms": NOW + 1,
                       "per_order_usd": "50", "daily_usd": "200"})


def test_encoding_matches_docs_examples():
    assert outcomes.encoding(1, 0) == 10
    assert outcomes.coin(1, 0) == "#10"
    assert outcomes.asset_id(1, 0) == 100_000_010
    with pytest.raises(ValueError):
        outcomes.encoding(1, 2)


def test_index_parses_template_fields():
    idx = outcomes.index(META)
    o = idx[20115]
    assert o["fields"]["shortName"] == "BTC-USD" and o["fields"]["threshold"] == "100000"
    assert o["quote"] == "USDC" and len(o["sides"]) == 2


@pytest.mark.parametrize("px,ok", [("0.42", True), ("0.12345", True), ("0.123456", False), ("1", False), ("0", False)])
def test_price_rule(px, ok):
    assert outcomes.price_ok(px) is ok


def test_preflight_builds_unsigned_spot_style_action():
    idx = outcomes.index(META)
    v, a = outcomes.preflight(M, DayState(), idx, outcome=20115, side=0, is_buy=True, px="0.42", sz="100", tif="Gtc",
                              now_ms=NOW)
    assert v.ok and a["orders"][0]["a"] == 100_000_000 + 201150 and a["orders"][0]["p"] == "0.42"
    v, a = outcomes.preflight(M, DayState(), idx, outcome=20115, side=0, is_buy=True, px="0.42", sz="200", tif="Gtc",
                              now_ms=NOW)
    assert not v.ok and a is None  # $84 > $50 per-order cap
    v, _ = outcomes.preflight(M, DayState(), idx, outcome=999999999, side=0, is_buy=True, px="0.5", sz="1", tif="Gtc",
                              now_ms=NOW)
    assert not v.ok


def test_builder_fee_on_outcome_sell_uses_spot_cap():
    idx = outcomes.index(META)
    v, a = outcomes.preflight(M, DayState(), idx, outcome=20115, side=1, is_buy=False, px="0.6", sz="10", tif="Alo",
                              now_ms=NOW, builder="0x" + "cd" * 20, fee_tenths_bp=500)
    assert v.ok and a["builder"]["f"] == 500


@pytest.mark.skipif(not os.environ.get("TALENTS_LIVE_TESTNET"), reason="set TALENTS_LIVE_TESTNET=1")
def test_live_outcome_meta_and_book():
    idx = outcomes.index(hl.info({"type": "outcomeMeta"}))
    oid = next(iter(idx))
    book = hl.info({"type": "l2Book", "coin": outcomes.coin(oid, 0)})
    assert book["coin"] == outcomes.coin(oid, 0) and len(book["levels"]) == 2
