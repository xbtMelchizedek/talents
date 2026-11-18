# Talents: a non-custodial trading layer for FLOP agents (pump.fun, Hyperliquid perps and outcomes, prediction markets)

Written Wed Nov 18 2026. Facts checked that day against official docs and chain reads.

**Labels.** **verified** = read this run from an official doc, official repo or chain RPC. **inferred** = my reading.
**unverified** = not checked. Not legal advice.

---

## 0. Top line

- **Each agent trades with its own keys, inside a signed mandate, and every call it makes is a signed, scoreable
  record.** No pooled funds, no custody by us or anyone, no bridge.
- **Hyperliquid is the backbone**: perps, HIP-4 outcome markets (predictions) and HyperEVM all live on one chain with
  public testnets. The new piece is **MandateTrader.sol**, a HyperEVM contract that *is* the agent's HyperCore
  account and enforces spend caps on-chain (allowed perps, per-order and daily notional caps, oracle band, expiry,
  pause, reduce-only), sending orders through HyperCore's CoreWriter. The agent's hot key can trade but can't move funds.
- **FLOP ⇄ HYPE/USDC with no bridge**: a hash-lock atomic swap. The FLOP leg is tclk's hash escrow; the HyperEVM leg
  is **HashLockEscrow.sol**. Same 32-byte sha256 preimage rule as pearl-diver, so the same secret opens both legs.
- **Callouts earn through builder codes**: followers' Hyperliquid orders can carry the caller's builder code (up to 0.1%
  on perps, 1% on spot, user-approved), which turns "call out trades, grow followers" into on-chain, auditable revenue.
  Track records come from commit-reveal signed callouts, scored by integer-only code that SP1 can prove.
- **pump.fun**: agents trade memes through Pump's own skills/SDK (they build, the agent signs), after the mandate check
  and a Curve-Lab simulation. Never create coins.
- **Prediction markets**: HIP-4 outcomes on Hyperliquid testnet first (verified live). Kalshi demo for the US-regulated
  route. Polymarket's international CLOB blocks new orders from US IPs; Polymarket US is a separate, KYC'd API.
- **IMD swarms get FLOP natively** through key-link: a seat's worker key *is* a FLOP account (path A), and a
  `flop` worker skill registers it on FLOP, buys and sells compute, and trades under a Talents mandate.

**Milestone 1 is built and public** (local + testnet reads only, nothing signed on any network): MandateTrader +
HashLockEscrow with 22 forge tests (incl. a fork test proving the real HyperEVM-testnet CoreWriter accepts our
encoding), a mandate engine, an HL testnet preflight CLI, signed callouts with golden vectors, and an anvil
end-to-end FLOP⇄HYPE swap. See §8.

---

## 1. Facts this design rests on

### 1.1 Hyperliquid (all verified 2026-11-18 from hyperliquid.gitbook.io and live testnet reads)
- **API / agent wallets** ([nonces-and-api-wallets](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets),
  [exchange endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)): a master
  account approves API wallets (`approveAgent`) that sign on its behalf or for sub-accounts. 1 unnamed + up to 3 named
  per account, +2 named per sub-account; `valid_until` expiry up to **180 days**. Agent-signable transfers are limited
  (`agentSendAsset`: "Destination must match the source address"). **Don't reuse agent addresses**: after
  deregistration the nonce set can be pruned and old signed actions can be replayed. Nonces: 100 highest kept per
  signer, must be within (T − 2 d, T + 1 d). Use one API wallet per trading process.
  *Inferred:* API wallets can't withdraw to other addresses (the docs list agent variants only for self-transfers and
  staking). Treat as unverified until tested on testnet.
- **There is no native spend cap on an API wallet.** Caps must come from (a) a sub-account funded only up to the cap,
  (b) our pre-sign mandate engine, or (c) MandateTrader on HyperEVM. Also available: **schedule cancel (dead man's
  switch)** on the exchange endpoint.
- **Builder codes** ([builder-codes](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/builder-codes)): per order
  `{"b": address, "f": tenths of a bp}`; user approves a max fee with `ApproveBuilderFee`, signed by the **main wallet,
  not an agent**; builder needs **≥ 100 USDC perps account value**; max **0.1% perps, 1% spot**; spot buys excluded;
  max 10 active approvals per user; fills per builder published daily at
  `stats-data.hyperliquid.xyz/Mainnet/builder_fills/{builder}/{YYYYMMDD}.csv.lz4`; claimed via referral rewards.
  HIP-4 outcomes: builder fees apply to **sell orders** that carry the code; outcome trading fees are currently zero.
- **HyperEVM** ([hyperevm](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm)): Cancun without
  blobs; mainnet chain 999, **testnet chain 998, RPC `https://rpc.hyperliquid-testnet.xyz/evm`** (read: `eth_chainId` =
  0x3e6). HYPE is gas, 18 decimals; priority fees burned.
- **CoreWriter** ([interacting-with-hypercore](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/interacting-with-hypercore)):
  system contract `0x3333…3333`, `sendRawAction(bytes)` (selector 0x17938e13 found in the testnet bytecode), ~47k
  gas. Encoding: version byte `1` | 3-byte big-endian action id | ABI fields. Action 1 = limit order
  `(uint32 asset, bool isBuy, uint64 limitPx, uint64 sz, bool reduceOnly, uint8 tif, uint128 cloid)`, px/sz × 1e8, tif
  1 Alo / 2 Gtc / 3 Ioc; 9 = add API wallet; 10 = cancel by oid; 12 = approve builder fee; 17 = outcome operation.
  Orders and vault transfers from CoreWriter are **delayed a few seconds** on purpose. The limit-order action has **no
  builder field**, so builder codes don't apply to CoreWriter orders (inferred from the field list).
- **Read precompiles** from `0x…0800` (positions, spot balances, **vault equity**, withdrawable, mark/oracle/spot px,
  L1 block number, account margin summary…). Oracle read on testnet: asset 3 → 0x9c374 = 639860 → 63,986.0 with
  szDecimals 5, matching `metaAndAssetCtxs` oraclePx at the same time. Precompile address names per the community
  [hyper-evm-lib](https://github.com/hyperliquid-dev/hyper-evm-lib) (the official page attaches L1Read.sol).
- **Vaults** ([vaults](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults)): the docs now steer builders to
  HyperEVM-tokenized vaults (e.g. ERC-4626) that trade through CoreWriter and delegate agents; HyperCore vaults are
  "legacy". HLP has a 4-day lock-up.
- **HIP-4 outcome markets** ([hip-4](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-4-outcome-markets)):
  fully collateralized Yes/No tokens settling in [0, 1], merged order books, no leverage or liquidations. First market:
  a recurring daily BTC binary. Deployer actions are testnet-only. **Testnet `outcomeMeta` returns live outcomes**
  (e.g. 20115, a BTC-USD threshold template; sports participants).
- **Testnet**: `metaAndAssetCtxs` lists 213 perps. **The testnet faucet (1,000 mock USDC) requires that the same
  address has deposited on mainnet.** That's a blocker for a fresh, separate key (§9).
- **Terms**: Hyperliquid's interface Terms list as **Restricted Persons anyone residing or located in the United States**
  (and Ontario), and forbid VPNs. Outcome markets have extra "Excluded Persons" rules.
- Official Python SDK: hyperliquid-python-sdk 0.24.0 (2026-06-04); testnet API `https://api.hyperliquid-testnet.xyz`.

### 1.2 pump.fun (verified 2026-11-17/18 from github.com/pump-fun and devnet RPC)
- Repos: pump-public-docs (IDLs `pump`, `pump_amm`, `pump_fees`), pump-fun-skills (create-coin, swap, coin-fees,
  tokenized-agents); skills **build** transactions and the user signs. npm: `@pump-fun/pump-sdk` 4.3.1,
  `@pump-fun/pump-swap-sdk` 2.2.0.
- **Devnet**: the bonding-curve program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` and PumpSwap
  `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` are executable on Solana devnet (getAccountInfo this run).
- Program changes to respect in simulation: buy_v3/sell_v3, multi-hop, negative virtual quote reserves, synthetic
  migration (from the build slate, verified Nov 18).

### 1.3 Prediction markets
- **Polymarket (international CLOB)** ([geoblock](https://docs.polymarket.com/api-reference/geoblock)): `GET
  https://polymarket.com/api/geoblock`; the **US is close-only** (no new orders) on frontend and API; VPNs prohibited.
  A US IP gets `{"blocked":true,"country":"US",...}`. py-clob-client carries contract config for Polygon (137)
  and Amoy (80002); a public staging CLOB host did not answer (`clob-staging.polymarket.com` no response): testnet
  CLOB availability **unverified**. Builder credentials exist in the client (`BUILDER_AUTH`), program details unverified.
- **Polymarket US** ([docs](https://docs.polymarket.us/api-reference/introduction)): separate API (`api.polymarket.us`,
  public data at `gateway.polymarket.us`), SDKs `polymarket-us` (pip/npm); keys need the iOS app and identity
  verification.
- **Kalshi** ([demo](https://docs.kalshi.com/getting_started/demo_env)): demo with mock funds,
  `https://external-api.demo.kalshi.co/trade-api/v2`; keys are per-environment; orders via `POST /portfolio/events/orders`
  (v1 order writes return 410). A CFTC-regulated exchange: the US-legal route, with KYC (inferred from its status;
  check Kalshi's own eligibility terms).

### 1.4 FLOP and IMD (from our earlier research, verified Nov 17–18)
- FLOP: Substrate (zkVerify-based devnet), no smart contracts, **won't build bridges**; third parties may connect it.
  Testnet target Nov 24. tclk hash escrow = the FLOP-side lock. AgentIdentity pallet on devnet. Faucet: agents 2 FLOP/24 h.
- IMD: Ethereum agent swarm, seats = ERC-8004 agents, worker drives the owner's own coding agent; jury mandates (worker
  0.1.32–0.1.37) with a first Sepolia action.

---

## 2. Architecture

```
            ┌───────────────────────── identity (key-link) ─────────────────────────┐
            │ did:key (Ed25519) == FLOP AccountId32 (path A)                         │
            │   ├─ HL master (EVM)  ── API wallet per process (named, valid_until)   │
            │   ├─ HyperEVM owner  ── MandateTrader.trader hot key                   │
            │   ├─ Solana hot key (holds only the pump cap)                          │
            │   └─ IMD seat device + EVM owner (path B, dual-signed)                 │
            └────────────────────────────────────────────────────────────────────────┘
 intent ─► MANDATE engine ─► SIMULATE ─► SIGN (agent's own key) ─► VENUE ─► fill ─► RECEIPT / CALLOUT ─► SCORE (SP1)
            (talents.mandate)  Curve-Lab     never us               HL perps / HIP-4 / pump / Kalshi
                               HL preflight                        HyperEVM HTLC  ◄─► FLOP tclk escrow
```

### 2.1 Keys and identity
- One agent = one did:key. Path A makes it a FLOP account directly. Venue keys are **separate** keys bound to it with
  key-link records (agentwork `keylink`, dual-signed), so a leaked venue key never exposes the FLOP identity and
  revocation is one signed record.
- Hyperliquid: the agent's EVM master key is cold (or the operator's); **each trading process gets a fresh named API wallet
  with `valid_until`**, never reused. Optional sub-account per strategy, funded only to its cap.

### 2.2 The mandate (three layers)
A signed JSON policy per agent (`talents.mandate`): network, venues, expiry, per-order and daily USD caps, max
leverage, allowed HL assets, max deviation from mark, reduce-only mode, pump caps (SOL in, slippage, mint denylist,
**never create**), swap cap, pause.
1. **Pre-sign engine** (in the agent; built): every intent is checked before anything is built or signed.
2. **Venue-native hard limits**: HL sub-account funded to the cap + API wallet + dead-man's-switch cancels; or
   **MandateTrader on HyperEVM** (built): the cap is a contract rule, so even a compromised agent key can't exceed it or
   withdraw. Owner-only `ownerAction` for transfers; guardian (e.g. a Watchman-style monitor) can pause, never unpause.
   Solana: the hot wallet only ever holds the pump cap.
3. **Simulation**: Curve-Lab replays pump transactions on a surfpool fork; HL preflight checks size/tick rules and the
   live mark.

### 2.3 Venue adapters
| Venue | Route | Status in M1 |
|---|---|---|
| HL perps (API wallet) | official SDK signs; our preflight builds the unsigned action | preflight + wire builder built (testnet reads) |
| HL perps (on-chain caps) | MandateTrader → CoreWriter action 1/10 | contract + tests + testnet fork test built |
| HL HIP-4 outcomes | outcome orders (spot-like asset ids) | mandate check built; adapter M2 |
| pump.fun | pump-fun-skills / pump-sdk build tx → Curve-Lab sim → agent signs | mandate check built; Curve-Lab M2 |
| Kalshi | demo REST, RSA-signed headers, the account owner's KYC'd keys | M2 (needs a demo account) |
| Polymarket | intl CLOB only where not geoblocked; Polymarket US for US with KYC | design only |
| FLOP ⇄ HYPE/USDC | HTLC: tclk hash escrow ⇄ HashLockEscrow | anvil + simulated FLOP built |

### 2.4 Swaps: FLOP for HYPE/USDC, then memes, with no bridge
- **HyperEVM leg** (`HashLockEscrow.sol`): lock id commits to every term; claim needs `sha256(preimage) == statement`
  with a 32-byte preimage; claim forbidden at/after `refundAfter`, refund allowed from it; anyone can submit a claim or
  refund (watchtowers) but funds only go to payee/payer.
- **FLOP leg**: tclk hash escrow (simulated in M1 by a Python port of pearl-diver's simtclk, pinned to the same
  scenario results).
- **Ordering**: the FLOP holder (secret owner) locks first with the longer window; rule
  `T_A(ms) ≥ T_B(s)·1000 + margin`. HyperEVM has timestamps, so no height-to-time conversion is needed (unlike Pearl).
- **"FLOP for memes"** is two hops, both non-custodial: FLOP → USDC on HyperEVM via HTLC, then USDC → a Solana meme. The
  Solana leg needs a second HTLC (a small Anchor program) or a market maker quoting FLOP ⇄ SOL directly. *No
  cross-chain message is ever trusted; only the revealed preimage moves.*
- **Who quotes**: market-maker agents post offers in Technocore `tclk-offers` (already a room); each settled swap emits
  an agentwork receipt, so MM reputation compounds. Known HTLC limits apply (free option, griefing, liveness); see
  pearl-diver's threat model.

### 2.5 Callouts, followers and earning
- **Commit, then trade, then reveal** (`talents.callout`, built): commit = sha256(domain | canonical call | 32-byte
  salt), signed by the agent's did:key, posted before entry; reveal later; first signed exit counts and can't be
  rewritten; **unrevealed commits past a grace window count as losses** (default −1,000 bps), so cherry-picking costs.
- **Score** is integer-only with truncation toward zero (calls, closed, wins, sum_bps, max drawdown, hidden), with
  golden vectors, so an SP1 program can reproduce it byte for byte (M2: `talents-rep` in SP1, like sp1-reputation).
- **Anchoring the PnL** (M2/M3): for HL accounts, a HyperEVM checkpoint contract reads the **vault-equity / account
  margin-summary precompiles** on a schedule and stores (L1 block, equity). The equity series is then on-chain data that
  anyone can recompute; SP1 proves the stats (return, drawdown, Sharpe-like ratios) over it and the proof verifies on
  HyperEVM (SP1VerifierGateway is deployed on HyperEVM per Succinct, from our Nov 17 research) or **natively on FLOP**
  (Proof Court, SP1 v5 shrink proofs).
- **Earning**:
  1. **Builder-code referral for callers**: a follower who acts on a call approves the caller's builder address once
     (main wallet, max fee) and their own orders carry `{"b": caller, "f": ≤10}`. The caller earns from followers' fills,
     verifiable in the public builder_fills dataset. No pooled money; the follower keeps their keys.
  2. HIP-4 builder fees on outcome sell orders.
  3. Swap spreads for MM agents (FLOP ⇄ HYPE/USDC).
  4. Later and gated: HyperEVM-tokenized vaults that followers deposit into (§7 legal flags).
- **Growing followers**: ranked leaderboard from proven records (FLOP AgentIdentity + ERC-8004 reputation entry);
  callout posts on X (human-approved) and signed lines on Technocore.

### 2.6 IMD swarms with native FLOP
- **Identity**: a seat worker's Ed25519 key is a FLOP account under path A; for seats whose identity is the EVM owner +
  device key, a path-B key-link record (device + EVM + FLOP, dual-signed) binds them. Built in agentwork (key-link v1).
- **Worker plugin (`flop` skill)**: register the linked account in FLOP `AgentIdentity` (testnet), take FLOP compute jobs,
  pay for inference in FLOP, and post agentwork receipts that feed both IMD reputation and FLOP `recordReputation`.
- **Trading under a mandate**: a swarm job type "trade within mandate M" runs Talents with the seat's own keys; jury
  mandates (Watchman pre-flight) can cover HyperEVM MandateTrader parameter changes, so a swarm treasury could set caps
  that only a jury quorum can raise.
- **Swaps**: IMD's Ethereum/Base side reaches FLOP the same way (HTLC on Base Sepolia ⇄ tclk), no bridge.

---

## 3. What's new here (prior art checked lightly; "none found" means my searches came up empty)
- On-chain-enforced spend caps for an AI agent's Hyperliquid account via CoreWriter: none found. (HL docs describe
  vaults delegating agents via CoreWriter; nothing with agent mandate semantics.)
- FLOP ⇄ HyperEVM hash-lock swaps: none found (FLOP has no bridges by policy).
- Commit-reveal signed callouts with a hidden-commit penalty and an SP1-provable score: none found for trading agents.
- Builder codes as a caller-referral revenue model: builder codes are widely used by frontends; using them to pay the
  *agent that made the call* is, as far as I found, new.

## 4. Risks
- **Legal (biggest)**: Hyperliquid's interface bars US persons; Polymarket intl blocks new US orders; HIP-4 outcomes add
  "Excluded Persons". Copy-trading, pooled vaults and
  paid signals can raise commodity-pool / advisor / gambling questions. Kalshi and Polymarket US are the US-regulated
  routes (KYC). This is tooling; who may use which venue from where needs real legal advice.
- **Market abuse**: callers could front-run or dump on followers. Mitigations: commit before entry, disclosed size
  buckets, cooldown before exiting against followers, no coordinated pumps, never create coins, no sniping or MEV.
- **Key risk**: API-wallet replay after pruning (never reuse), hot-key theft (caps + MandateTrader), seed leakage (keys
  stay on the agent's box, mode 600, never in chat/git/logs).
- **Protocol**: CoreWriter delays orders a few seconds; precompiles return errors and burn gas on bad inputs; whether
  HyperCore accepts actions from a fresh contract account before it is funded is unverified; liquidation risk on perps;
  pump.fun program changes; HTLC free option and griefing; FLOP testnet surfaces may differ from devnet.
- **Data honesty**: unrevealed commits, survivorship, and off-chain fills. Anchor to on-chain equity where possible.

## 5. Milestones
- **M1 (built, local + testnet reads, nothing signed on any network)**: see §8.
- **M2 (testnet, separate throwaway keys, needs testnet funds)**: HL testnet API-wallet trading through
  the official SDK with the preflight; deploy MandateTrader + HashLockEscrow on HyperEVM testnet; HIP-4 outcome adapter;
  Curve-Lab on surfpool for pump devnet; real FLOP testnet tclk escrow after Nov 24; SP1 `talents-rep` program.
- **M3**: equity checkpoint contract + SP1 PnL proof verified on HyperEVM testnet and on FLOP (Proof Court); Kalshi demo
  adapter; Solana HTLC; leaderboard.

## 6. Open questions
- Does HyperCore accept CoreWriter orders from a contract account that was funded by spot send? (testnet M2)
- Do API wallets truly lack withdraw rights? (docs imply; test on testnet)
- Will FLOP testnet expose tclk hash escrow to third parties on day one?
- Kalshi and Polymarket builder/referral programs: terms unverified.

## 7. Dependencies outside this repo
- **HL testnet funds**: the faucet requires a mainnet deposit from the same address, so a fresh key can't get mock USDC.
- **Jurisdiction**: which venues a given operator may lawfully use (US restrictions above).
- **A builder address** for the referral model needs ≥ 100 USDC perps account value.
- A Kalshi demo account for the Kalshi adapter.

## 8. Milestone 1 as built (repo `talents`)
- `contracts/src/MandateTrader.sol`, `contracts/src/HashLockEscrow.sol`; forge: 21 unit/fuzz tests + 1 HyperEVM
  testnet **fork** test (local fork, nothing broadcast) showing the real CoreWriter emits its RawAction log for our
  exact encoding.
- `talents/mandate.py` (policy engine; refuses mainnet), `talents/hl.py` (testnet-pinned info client, wire builder,
  tick/lot rules from the docs, builder-fee caps, preflight), `python -m talents preflight …` CLI.
- `talents/callout.py` + `vectors/callout-v1.json` (4 golden cases, PUBLIC test keys).
- `talents/flopsim.py` (pinned to pearl-diver's 7 scenarios) + `talents/swap.py` + anvil end-to-end tests (happy path,
  bad timing, mismatched FLOP lock, short HyperEVM lock, walk-away refunds, near-deadline refusal, no-lock refund).
