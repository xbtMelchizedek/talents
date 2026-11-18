"""Pin the Python FLOP rail to the exact results pearl-diver's Go simtclk (and tclk's MemoryRail) give."""
import hashlib
import json

from conftest import ROOT
from talents.flopsim import LockTerms, RailError, SimFlopRail

WANT = {  # copied from pearl-diver simtclk/conformance_test.go TestScenariosPinned
    "happy": [True, True, True, False, False],
    "wrong-preimage": [True, False, False, True],
    "uppercase-hex-preimage": [True, True],
    "refund-timing": [True, False, False, True, False],
    "late-lock": [False, True, False],
    "verify-mutated": [True, False],
    "claim-after-claimby-before-refund": [True, True],
}


def run(scenarios):
    ph = hashlib.sha256(b"pearl-diver-conformance").digest()
    pre = "0x" + ph.hex()
    stmt = "0x" + hashlib.sha256(ph).hexdigest()
    secrets = {"good": pre, "bad": "0x" + "00" * 32, "short": "0x" + pre[2:40], "upper": "0x" + pre[2:].upper()}
    out = {}
    for s in scenarios:
        now = [0]
        r = SimFlopRail(clock=lambda: now[0])
        tm = LockTerms(contract=stmt, statement=stmt, amount="1000", asset="FLOP", payer="did:key:payer",
                       payee="did:key:payee", claim_by_ms=500, refund_after_ms=1000)
        res = []
        for st in s["steps"]:
            now[0] = st["at"]
            op = st["op"]
            try:
                if op == "lock":
                    r.lock(tm); res.append(True)
                elif op == "verify":
                    t2 = LockTerms(**{**tm.__dict__, "amount": "999"}) if st.get("mut") else tm
                    res.append(r.verify_lock(t2, stmt))
                elif op == "claim":
                    r.claim(stmt, secrets[st["secret"]]); res.append(True)
                elif op == "refund":
                    r.refund(stmt); res.append(True)
            except RailError:
                res.append(False)
        out[s["name"]] = res
    return out


def test_matches_go_simtclk():
    scen = json.loads((ROOT / "vectors" / "flop_rail_scenarios.json").read_text())
    assert run(scen) == WANT
