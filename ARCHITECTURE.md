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

### Evidence-domain allowlist

The anchor check alone is not enough: a party could write a page that contains the expected address and submit it as evidence. So `create_trade` also records `allowed_evidence_domains`, a comma-separated list normalized to lowercase hostnames. `party_b` sees it before matching the stake. `submit_evidence` parses each URL with `urllib.parse.urlsplit` and rejects any URL that is not https, has embedded credentials (`https://allowed.com@evil.example/`), or whose hostname is neither an allowed domain nor a subdomain of one (`notetherscan.io` and `etherscan.io.evil.example` do not match `etherscan.io`). One bad URL rejects the whole submission, and at least one domain is required.

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

- The allowlist trusts whoever operates the listed domains. It prevents self-hosted evidence, not a mistake in what a listed explorer shows.
- Some explorer pages are rendered with JavaScript, so GenVM's plain HTML fetch may not see the address. Choose domains (or API endpoints on them) whose pages contain the data as server-rendered text.

- The anchor-match backstop only prevents a false-positive
  `TRADE_COMPLETED`; it does not independently re-derive the correct
  outcome, so a determined leader could still return
  `PARTIALLY_COMPLETED` or `TRADE_BREACHED` inconsistent with the
  actual evidence — the same limitation any LLM-judged outcome has,
  which is exactly what the Tier-2 jury layer exists to catch on
  appeal.
- `expected_amount` is a free-text string compared by the LLM, not a
  parsed numeric field with unit normalization — a v1.1 candidate.

## Evidence binding at a glance

- Consensus-bound (validators re-run the fetch and judgment and compare these exactly in code via a custom `gl.vm.run_nondet_unsafe` equivalence, with no LLM comparator; validators also check that the leader's stored `frozen` pages are exactly the pages its manifest describes, so a leader cannot store doctored evidence): `outcome`, `anchor_match`, `amount_match`, `structured_verified`, `pages_with_address`, and the `evidence_manifest` of `[url, excerpt_sha256, tx]` per page. These are reproducible across validators.
- Integrity-bound (committed in `evidence_content_root`, checked by `_frozen_intact` before Tier 2): the manifest plus `full_content_sha256`, the hash of each whole fetched page as the resolver saw it. It is not a comparative consensus field because dynamic explorer pages can legitimately differ between validator fetches (for example a growing `confirmations` counter), which would make `resolve_tier1` fail for harmless reasons. This is a deliberate choice, not an omission.

## 5. Hardening notes

Evidence and settlement
- Evidence is two-sided. Each party may submit one list (up to 5 https URLs on the allowed domains). The first submission does not freeze anything: the counterparty gets a grace period (`EVIDENCE_GRACE_SECONDS`, counted from the later of the first submission and the trade deadline, never past `evidence_deadline`) to add its own list, so one side alone can never freeze the evidence before the other side has had until the deadline to perform. `evidence_deadline` itself is `EVIDENCE_WINDOW_SECONDS` after the later of acceptance and the trade deadline, so a payment made at any time before the deadline can still be proven. The snapshot freezes when both have submitted, or when anyone calls `lock_evidence` after the grace period. This stops one side from locking a weak or irrelevant source set before the other can answer.
- `resolve_tier1` checks the expected receiving address in the fetched text itself, in code. A `TRADE_COMPLETED` outcome needs both the model's `anchor_match` and a literal, case-insensitive match of the address in the evidence. The model's claim alone is never enough.
- The Tier-1 prompt receives the trade deadline, and is told that missing or unrelated evidence means `UNDETERMINED`; `TRADE_BREACHED` needs evidence that affirmatively shows non-performance.
- The consensus principle requires the outcome and `anchor_match` to match exactly across validators; only the wording of the reasoning may differ.
- `expire_unevidenced` closes an accepted trade as `UNDETERMINED` and refunds both stakes when no evidence was locked before `evidence_deadline`. Without it, stakes could stay escrowed forever. If evidence was submitted and can still be locked, it locks it instead, so a party cannot race `lock_evidence` to discard the other side's proof.
- Premature resolution: before the trade deadline only a code-confirmed `TRADE_COMPLETED` can be recorded. Any other result makes `resolve_tier1` revert, leaving the trade `evidence_locked` so it can be resolved again after the deadline; a party that has not performed therefore cannot void the trade early (an early `UNDETERMINED` would refund its stake). Tier 2 applies the same rule using the time the evidence was frozen (`tier1_resolved_at`), not the time the jury finalizes.
- Model output that is not valid JSON is treated as `UNDETERMINED` instead of reverting, so a malformed answer cannot wedge a trade.
- `expire_unresolved` closes a trade whose Tier 1 never produced an outcome within `RESOLUTION_TIMEOUT_SECONDS` of the later of the evidence lock and the trade deadline (`UNDETERMINED`, both stakes refunded). `expire_stuck_appeal` does the same for a jury round that cannot finalize: the Tier-1 outcome stands, the bond is refunded and jurors are unlocked. Both are permissionless.
- `accept_trade` is rejected after the trade deadline. Input sizes are bounded (text, URL length, number of URLs and domains), so a trade cannot be made too large to resolve.

Settlement table (each party stakes the same amount; `pot` is both stakes)
- `TRADE_COMPLETED`: `party_b` receives the pot.
- `TRADE_BREACHED`: `party_a` receives the pot.
- `PARTIALLY_COMPLETED`: the pot is split evenly.
- `UNDETERMINED`: each party gets its own stake back.

Jury
- Commit hashes must be 64-character SHA-256 hex (stored lowercase) and salts are capped at 256 characters. The commit is `sha256("<vote>:<salt>:<juror address, lowercase>:<trade_id>")`. Binding the juror and the case into the hash means a juror who copies another juror's public commit can never reveal it, so free-riding on someone else's vote is impossible.
- Slashed stake never stays stranded: when no verified majority exists, the stake slashed from non-revealers is paid pro-rata to the jurors who did reveal, or to the appellant when nobody revealed.
- `party_a` and `party_b` are excluded from jury selection for their own trade.
- A juror selected for an appeal has a locked stake until `finalize_jury` runs, so stake cannot be withdrawn to dodge slashing.
- Jury pool snapshot: only jurors whose first `registered_at` and last stake increase (`stake_updated_at`) are at or before the trade's `accepted_at` are eligible, so a party can neither register fresh addresses nor buy extra draw weight after seeing a dispute coming. The snapshot keeps at most `MAX_JURY_POOL` (100) of the heaviest eligible jurors, which bounds the work and storage of `appeal` and `draw_jury`.
- Tier-2 verification is independent. Validators re-fetch the frozen evidence and derive their own outcome without seeing the Tier-1 reasoning or the jury's candidate; the majority is verified only when that outcome equals it exactly. The same code-level address and amount checks and the same premature-resolution rule as Tier 1 apply.
- `expected_amount`: when set, its numeric part (commas ignored) must appear as a whole number within 300 characters of an occurrence of the expected address in the fetched text, so `100` does not match `1000` and a figure elsewhere on the page does not count. Both Tier-1 and Tier-2 prompts also require the address to be the recipient of one specific transfer and the amount to belong to that same transfer. The code check is textual, not a parsed transaction.
- Structured transaction proof: when an evidence URL returns a JSON transaction record in the Blockscout v2 shape (`status`, `result`, `timestamp`, `to.hash`, `value`, and `token_transfers[]` with `to.hash`, `total.value`, `total.decimals`, `token.symbol`; for example `https://eth.blockscout.com/api/v2/transactions/<hash>`), the contract parses it in code and keeps the facts (recipient, raw amount, decimals, symbol, status, time) in the frozen evidence. `TRADE_COMPLETED` then needs a successful transaction made between trade creation and the deadline, paid in the asset the trade recorded: the native coin when `expected_token_contract` is empty, otherwise a token transfer whose token contract equals `expected_token_contract` exactly (a worthless token can copy a symbol but not a contract address) (an older transfer to the same address cannot be replayed as proof) with a transfer to the expected address of exactly the expected amount (integer arithmetic, so `250.5` equals raw `250500000` at 6 decimals), and the asset symbol must match when the amount names one (native coin transfers accept ETH, GEN, BNB, MATIC, POL, AVAX or no symbol; native amounts may also be written in `wei` or `gwei`, e.g. `31337 wei`). Without `require_structured_proof`, a verified structured record or the text check over the pages that are not structured records proves completion, so an unrelated transaction record submitted by one party cannot veto a completion the other party proved in text. Validators must agree on the `structured_verified` flag. Use an API endpoint on an allowed domain for the strongest evidence. A trade can set `require_structured_proof` at creation; then `TRADE_COMPLETED` is impossible without a structured record that passes these checks, and the text fallback is disabled for that trade. The amount must be exactly `<number>` or `<number> <SYMBOL>` (validated at creation, so free text such as `250 USDT or 300 DAI` is rejected), and `expected_token_contract` must be a 0x-prefixed 20-byte address. A structured record is frozen as its canonical parsed facts (sorted JSON of hash, status, time and transfers), not the raw explorer JSON, so volatile fields such as a growing `confirmations` counter cannot break consensus. One payment can settle only one trade: the transaction hash that proves a completion is recorded in `payment_claims`, and a later trade citing the same transaction resolves `UNDETERMINED`.
- `min_evidence_sources` counts distinct hosts, not URLs, so five pages on one explorer are one source.
- Economic floors: `MIN_STAKE`, `MIN_JUROR_STAKE` and `MIN_APPEAL_BOND` are 1e15 wei (0.001 GEN). Offline tests lower them to 1 wei.
- Evidence content is frozen at Tier 1, and what is frozen is exactly what validators agree on. For each page the frozen form is canonical: for a JSON transaction record, the canonical sorted JSON of its parsed facts (hash, status, time, and each transfer's recipient, raw amount, decimals, symbol and token contract); for any other page, whitespace-normalised text reduced to 400 characters either side of up to 5 occurrences of the expected address (or the first 1500 characters when the address does not appear). The manifest `[url, excerpt_sha256, tx]` per page (`excerpt_sha256` is the hash of the canonical frozen excerpt; a hash of the whole fetched page is deliberately not committed, because it would change with every timestamp or counter on the page) is what validators compare exactly in code, and `evidence_content_root` commits to that same manifest together with `full_content_sha256`, the hash of each whole fetched page as the resolver saw it. The whole-page hashes are audit and tamper evidence and are deliberately not part of validator consensus: a live page carries values that change between fetches (the Blockscout API returns a `confirmations` counter that grows every block), so requiring those hashes to match would make resolution fail for harmless reasons. What validators agree on is the manifest, byte for byte the evidence that is stored and later committed to. `evidence_url_set_hash` in the snapshot is a different, earlier value: the hash of the sorted URL list taken at `lock_evidence`. Validators re-run the fetch and the judgment themselves and also require the leader's reasoning to be a string of at most 2000 characters. Tier 1 validators must also agree on the outcome, the anchor and amount flags, `structured_verified`, and which pages contained the address. Tier 2 first re-hashes the stored content against the root (a mismatch fails verification), then judges that stored content and never re-fetches, so a site changing after Tier 1 cannot change the jury verdict. Between `lock_evidence` and `resolve_tier1` the pages are still live; `resolve_tier1` is permissionless and can run immediately after the lock. Consequence: if a page's text near the address changes between two validators' fetches, they disagree and the resolve call fails and can be retried; `expire_unresolved` refunds both stakes after 7 days.
- Jury randomness is drawn from a future drand round. `appeal` only escrows the bond and records the round that follows the end of the appeal window (`draw_round`); `draw_jury` (permissionless, after the window) fetches exactly that round, so nobody can time an appeal to a beacon they have already seen. The pool and weights used are stored in the jury record and `verify_jury_selection` replays the draw from them. A filed appeal that is never drawn is recovered by `expire_stuck_appeal`.
- Selection uses integer arithmetic only: jurors are ordered by address, and each draw maps `sha256(beacon:case:draw) mod total_weight` onto the cumulative weights, removing the picked juror before the next draw. There is no floating point, so every validator computes the same jury. Weights are integers (capped stake times a reputation multiplier in per-mille). A test fails if selection stops depending on the beacon.

Known limitations
- Building the eligible pool still reads every juror record, so a very large number of registered jurors makes `appeal` more expensive; the snapshot itself is capped at the 100 heaviest jurors, which also means very small jurors are not drawn once 100 heavier ones exist (selection is stake-weighted in any case).
- With `require_structured_proof` a token amount must name the token contract at creation (`expected_token_contract`); without it a token amount may be proven by text only, as for non-EVM assets such as BTC.
- Payment de-duplication covers structured transaction records only. Text-only evidence carries no transaction identity, so a trade that relies on the text fallback should use a unique amount or receiving address; `require_structured_proof` (on by default in the dApp) avoids this entirely. A payment claimed by a trade stays claimed even if a jury later overturns that trade's outcome.
- Time: every deadline check goes through one function, `_now_utc()`, which reads the transaction timestamp from `gl.message_raw["datetime"]` and falls back to `datetime.now()` where that is unavailable. Inside GenVM this clock is deterministic and pinned to the transaction timestamp, identical for every validator (see the GenLayer transaction-context documentation), so deadline checks are consensus-safe. They measure transaction time, not real-world time.
- The juror pool and weights are frozen when the appeal is filed (`appeal.eligible_pool`), and every juror in it is held (`pool_holds`) so they cannot unstake until `draw_jury` runs or the appeal is recovered. `draw_jury` therefore uses the snapshot exactly as recorded: nobody is added, no weight or stake changes, nobody drops out. Selected jurors then get the usual `locked_cases` lock until the jury finalizes. The pool, weights and beacon are recorded in the jury record, so the draw is replayable.
- Only the Blockscout v2 JSON shape is parsed structurally. For any other explorer or an HTML page, the address and amount checks are proximity-based text checks: they show the address and amount appear together on the page, not that they belong to one parsed transfer, and the recipient role relies on the model.
- Hosts are compared by exact hostname, not registrable domain, so `a.example.com` and `b.example.com` count as two sources.
- The jury beacon comes from the public drand endpoint through the validators' web fetch, not an on-chain VRF; whoever can influence that endpoint's responses to validators could influence the draw.
- Evidence content is frozen at the first `resolve_tier1`, not at `lock_evidence`, because validators cannot agree on a byte-exact page at lock time without making locking fragile.
- `MIN_JUROR_STAKE` is small and refundable when no case is open, so a well-funded attacker could register several addresses. Selection weight is capped and parties are excluded, but this is not a full Sybil defense.
- The Studio runtime keeps GEN attached to a payable call that reverts. The frontend always sends the exact required amount, but a manual call with a wrong value can strand that GEN.
- A listed evidence domain may serve mutable content (for example a raw file on a branch). The snapshot freezes the URLs, not the page bytes.
- The jury randomness beacon is fetched from a public drand endpoint, as in Veridict.
