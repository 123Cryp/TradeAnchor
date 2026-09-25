# TradeAnchor — Architecture

## 1. Relationship to Veridict

TradeAnchor is a sibling of Veridict, not a rewrite from scratch. The
two-tier adjudication core (Tier-1 automated outcome, frozen
`EvidenceSnapshot`, staked commit-reveal jury, bounded GenLayer
plausibility check on the jury's majority, on-chain bounded reputation,
credit-then-withdraw payouts via `pending_withdrawals`/`withdraw()`) is
carried over unchanged, because it already shipped and was accepted
without a steward rejection. Reusing a proven-safe mechanism instead of
inventing a new one is a deliberate risk-reduction choice, not laziness
— re-litigating already-solved consensus/access-control problems is
exactly where prior projects in this series (Tribunal, Covenant,
Vigil, Cascade, ...) picked up steward findings.

## 2. What's actually new: on-chain evidence anchoring

Veridict judges a freeform `objective` against freeform evidence, with
nothing mechanical to cross-check. An OTC trade dispute almost always
reduces to one checkable fact: did value arrive at a specific address,
for a specific amount? So:

- `create_trade` takes two new fields, `expected_receiving_address`
  (required) and `expected_amount` (optional — blank for a
  no-fixed-amount best-effort swap), recorded on-chain **before**
  either party knows how the dispute will resolve.
- `resolve_tier1`'s prompt requires the model to check the fetched
  evidence against this recorded address/amount and return
  `anchor_match: true/false` alongside its outcome.
- A **deterministic code-level backstop** (plain Python, not a prompt
  instruction) forces any `TRADE_COMPLETED` claim down to
  `UNDETERMINED` if `anchor_match` isn't `true`. This matters because a
  prompt instruction alone is something an LLM can simply fail to
  follow; the backstop can't be talked out of it.
- The Tier-2 jury-plausibility check (`_verify_majority_against_evidence`)
  applies the same rule: a `TRADE_COMPLETED` majority is never
  "plausible" unless the address is actually supported by the evidence
  or Tier-1's own reasoning.

This is the genuine differentiator from Veridict — not a reskin — and
is the reason this project exists as its own submission rather than
being folded into Veridict's v1.1 roadmap.

## 3. Deliberately out of scope for v1

- No relay/Base/USDC leg — same v1 simplification as Veridict, for the
  same reason (buildable and testable from a phone browser + GenLayer
  Studio, no Solidity toolchain available in this environment).
- No structured multi-field evidence beyond one address + one amount
  string (e.g. token contract address, chain ID, minimum confirmation
  count). A real block-explorer-parsing layer is a natural v1.1
  extension once there's live-testing signal on what stewards or real
  disputes actually need.
- Escalation to a larger jury on a failed Tier-2 verification falls
  back to holding the Tier-1 outcome, same v1 simplification as
  Veridict (see its class docstring).

## 4. Known limitations (disclosed, not hidden)

- The anchor-match backstop only prevents a false-positive
  `TRADE_COMPLETED`; it does not independently re-derive the correct
  outcome, so a determined leader could still return
  `PARTIALLY_COMPLETED` or `TRADE_BREACHED` inconsistent with the
  actual evidence — the same limitation any LLM-judged outcome has,
  which is exactly what the Tier-2 jury layer exists to catch on
  appeal.
- `expected_amount` is a free-text string compared by the LLM, not a
  parsed numeric field with unit normalization — a v1.1 candidate.
