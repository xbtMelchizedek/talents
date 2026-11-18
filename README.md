# Talents

A non-custodial trading layer for [FLOP](https://docs.flop.network) agents: Hyperliquid perps and HIP-4 outcome
markets, pump.fun memes, prediction markets, and FLOP ⇄ HYPE/USDC atomic swaps with no bridge. Every agent trades
with its own keys inside a signed mandate, and every call it makes is a signed record anyone can score.

> "Thou hast been faithful over a few things, I will make thee ruler over many things." (Matthew 25:21)
> Agents start with small, capped mandates and earn bigger ones with a provable record.

> **Status: milestone 1. Local, Hyperliquid testnet reads, and a local fork only.** Nothing here signs or submits
> anything on any network. Mainnet endpoints are refused in code. Unaudited. Don't use with real funds.

Design: [docs/DESIGN.md](docs/DESIGN.md) (facts verified against official docs on 2026-11-18, with links).

## What's in milestone 1

| Piece | What it does | Evidence |
|---|---|---|
| `contracts/src/MandateTrader.sol` | A HyperEVM contract that **is** the agent's HyperCore account. The agent's hot key can place and cancel orders via CoreWriter only inside on-chain caps: allowed perps, per-order notional, daily notional, oracle band, expiry, pause, reduce-only. The hot key can't move funds. | 10 forge tests incl. fuzz; a local fork of HyperEVM **testnet** shows the real CoreWriter (`0x3333…3333`) emits its log for our exact action bytes |
| `contracts/src/HashLockEscrow.sol` | The HyperEVM leg of a FLOP ⇄ HYPE/USDC hash-lock swap. 32-byte sha256 preimage, the same rule as tclk and [pearl-diver](https://github.com/xbtMelchizedek/pearl-diver). | 11 forge tests incl. fuzz |
| `talents/mandate.py` | The pre-sign policy engine: HL perps, HIP-4 outcomes, pump.fun buy/sell (never create), swaps. Refuses mainnet networks. | `tests/test_mandate.py` |
| `talents/hl.py` + `python -m talents preflight` | Testnet-pinned info client, order wire builder (SDK shape), tick/lot rules and builder-fee caps from the docs, preflight against the live testnet mark. Prints the **unsigned** action. | `tests/test_hl.py` (live read-only test behind `TALENTS_LIVE_TESTNET=1`) |
| `talents/outcomes.py` | HIP-4 outcome markets on testnet: asset ids (`100_000_000 + 10*outcome + side`, from the docs), template parsing, preflight to an unsigned order. | `tests/test_outcomes.py` |
| `talents/callout.py` | Commit-reveal signed callouts (did:key Ed25519) and an integer-only track record (hidden commits count as losses) that an SP1 program can reproduce. | `vectors/callout-v1.json` golden vectors |
| `talents/flopsim.py`, `talents/swap.py` | Simulated FLOP hash escrow (pinned to pearl-diver's scenarios) and a swap coordinator; end-to-end on anvil. | `tests/test_swap_anvil.py` |

## Run it

```bash
cd contracts && forge build && forge test && cd ..
pip install pytest cryptography web3
python -m pytest -q
# optional, read-only calls to Hyperliquid testnet:
TALENTS_LIVE_TESTNET=1 python -m pytest -q tests/test_hl.py
python -m talents preflight --mandate examples/mandate.testnet.json --coin BTC --buy --px 63500 --sz 0.003 --lev 2
# optional, local fork of HyperEVM testnet (nothing broadcast):
cd contracts && HYPEREVM_TESTNET_FORK=1 forge test --match-contract CoreWriterFork --fork-url https://rpc.hyperliquid-testnet.xyz/evm -vv
```

## Honest limits
- The FLOP leg is simulated until FLOP testnet (target Nov 24, 2026) exposes tclk's hash escrow.
- Whether HyperCore accepts CoreWriter orders from a fresh contract account is untested (needs testnet funds).
- The Hyperliquid testnet faucet requires a prior mainnet deposit from the same address, so testnet orders wait.
- Jurisdiction: Hyperliquid's interface Terms bar US persons; Polymarket's international API blocks new US orders.
  Check what applies to you. This is software, not advice.
- Security issues: please report privately (open a GitHub security advisory), not in a public issue.

## License
MIT
