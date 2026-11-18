# Contract testing

Three layers, all run by `forge test` in `contracts/`:

| Layer | Where | What it proves |
|---|---|---|
| Unit and adversarial | `test/*.t.sol` | Golden encodings, each revert path, composition cases |
| Fuzz (stateless) | `test/fuzz/` | One property per test; each expected value is computed a second way |
| Invariant (stateful) | `test/invariant/` | Properties that must hold after any sequence of calls |

Invariant handlers drive bounded calls from named actors, keep an independent
model of the contract (ghost state), and never revert themselves, so
`fail_on_revert = true` turns any surprise into a failure. Each handler counts
disagreement in both directions: the contract accepted what the model forbids,
or refused what the model allows. Both must stay zero. The shape follows
morpho-blue and yearn-vaults-v3; the weird-token fixtures follow Solady. Our
code, not theirs.

## Properties in plain words

HashLockEscrow
- The escrow holds exactly the sum of open locks, in ETH and in each token. No shortfall, no dust.
- Value is only moved between actors and the escrow, never created or lost.
- Every lock's stored terms and status match the model; a settled lock stays settled.
- No wrong preimage, claim at or after the deadline, refund before it, double settle, or short payment ever succeeds.
- At any moment exactly one exit is open: claim strictly before `refundAfter`, refund at or after it.
- The lock id changes if any single term changes.
- Tokens that return nothing still round-trip; tokens that return `false` cannot lock.

MandateTrader
- New-risk notional placed on any UTC day never exceeds the daily cap and equals the model's total exactly.
- `remainingToday()` is always the cap minus today's spend.
- The contract and the model agree on every order attempt (pause, expiry, allowlist, shape, size, oracle band, cap).
- Only orders and cancels reach CoreWriter, with header version 1 and action id 1 or 10; nobody but the trader key places orders, and only the owner sends raw actions.
- The oracle band is inclusive at both edges and closed one unit outside them.
- Reduce-only orders never draw down the cap; notional equals `limitPx * sz / 1e8`, computed without the full product.

## Mutation check

Before merging a new property, break the contract on purpose and confirm the
suite fails. For this suite we flipped the claim deadline comparison, dropped
the `Claimed` status write, loosened the daily cap by $1, charged reduce-only
orders to the cap, let the guardian place orders, removed the pause check, and
widened the oracle band. Each mutant was caught.

## Depth

- Local: `forge test` (512 fuzz runs, 256 invariant runs x depth 64).
- CI: `FOUNDRY_PROFILE=ci forge test` (10,000 fuzz runs, 1,000 invariant runs x depth 64).
