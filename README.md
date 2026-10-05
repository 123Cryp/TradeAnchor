# TradeAnchor

A two-tier adjudication protocol for peer-to-peer OTC crypto trades, built
as a single GenLayer Intelligent Contract. A fast automated outcome
(Tier 1) is backed by an economically-accountable, staked commit-reveal
jury appeal layer (Tier 2) — with **on-chain evidence anchoring** so a
"trade completed" outcome can never stand on an unrelated or
self-serving explorer link.

## Why this exists

OTC crypto trades (crypto-for-fiat, crypto-for-crypto, cross-chain swaps
arranged off an exchange order book) fail in a very specific way:
"did the crypto actually land at the agreed address, for the agreed
amount?" That's a fact you can check against a block explorer, not a
matter of taste — so a generic freeform-evidence dispute contract is
leaving an easy, mechanical check on the table.

## What's different from a plain dispute contract

`create_trade` records `expected_receiving_address` and
`expected_amount` **on-chain, at trade creation, before either party
knows how the dispute will go**. Both the Tier-1 automated outcome and
the Tier-2 jury-plausibility check are instructed to reject a
`TRADE_COMPLETED` outcome unless the fetched evidence actually shows
that address — and there's a **deterministic code-level backstop**
(not just a prompt instruction) in `resolve_tier1`: if the model claims
`TRADE_COMPLETED` without confirming `anchor_match`, the outcome is
forced down to `UNDETERMINED` regardless of what the model says.

`create_trade` also records `allowed_evidence_domains` on-chain (for example `etherscan.io, basescan.org`). The counterparty sees the list before accepting, and `submit_evidence` rejects any URL that is not https, carries embedded credentials, or is not on one of those domains or their subdomains. A party therefore cannot host its own page containing the expected address and submit it as evidence.

Everything else — the frozen `EvidenceSnapshot`, the staked
commit-reveal jury, the bounded GenLayer plausibility check on the
jury's majority, on-chain bounded reputation, and strict
credit-then-withdraw payouts — is the same architecture as the sibling
project **Veridict**, reused unchanged because it already shipped
without a steward rejection.

## Lifecycle

```
created -> open (accepted) -> evidence_locked -> tier1_resolved
   -> [appeal_window] -> finalized (no appeal)
                             |
                             v (appeal + bond, within window)
                          appealed -> committing -> revealing
                             -> finalized -> settled
```

Escape hatches: `cancelled` (before acceptance) and — like every prior
project in this series — no state ever neither pays nor refunds.

## Outcomes

`TRADE_COMPLETED` / `PARTIALLY_COMPLETED` / `TRADE_BREACHED` /
`UNDETERMINED`

## Contract

- `contract.py` — the full, documented source.
- `contract_deploy.py` — the same logic with comments/docstrings
  stripped via AST/tokenize (not regex — regex stripping has
  previously corrupted real string-literal return values in a sibling
  project). Deploy this one to GenLayer Studio; a heavily-commented
  `.py` file has been observed to fail Studio's schema check with a
  generic `invalid_contract` error even when the Python is valid.

## Tests

38 offline unit tests against a self-written `genlayer` SDK stub
(`tests/genlayer_stub/`), no `pip install` required:

```
python3 -m unittest discover -s tests -v
```

Covers the full lifecycle, evidence-quality/deduplication rules, jury
selection/commit-reveal/slashing/reputation, and — specific to this
project — the on-chain anchor-match backstop
(`tests/test_anchor_match.py`) and the evidence-domain allowlist (`tests/test_evidence_domains.py`).

## Live deployment

GenLayer Studio: `0xd38960D5CD8afB30A531A1989F1A30ECc706893a`
(https://explorer-studio.genlayer.com/address/0xd38960D5CD8afB30A531A1989F1A30ECc706893a)

Verified live:

- Evidence on an allowed domain that contains the expected receiving address: `TRADE_COMPLETED` with `anchor_match: true`.
- Evidence on a domain outside the trade's allowed list (including look-alike hosts such as `allowed.com.evil.example`): `submit_evidence` is rejected and the trade stays `open` with no evidence locked.
- Evidence that does not contain the expected address: forced down to `UNDETERMINED` by the code-level backstop (verified on an earlier deployment of the same logic).

## Status

Contract and 38 offline tests complete and passing. The commit-reveal jury appeal path is inherited unchanged from Veridict and is covered by offline tests; it has not been exercised live on this deployment.
