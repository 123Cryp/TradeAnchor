# TradeAnchor

A two-tier adjudication protocol for peer-to-peer OTC crypto trades, built as a single GenLayer Intelligent Contract. An automated outcome (Tier 1) is backed by a staked, commit-reveal jury appeal (Tier 2). Evidence is anchored on-chain, so a "trade completed" outcome cannot stand on an unrelated or self-serving explorer link.

## Why this exists

OTC crypto trades fail in a specific way: "did the crypto actually land at the agreed address, for the agreed amount?" That is a fact you can check against a block explorer, not a matter of taste. A generic freeform-evidence dispute contract leaves that mechanical check on the table.

## What is different from a plain dispute contract

- **On-chain anchor.** `create_trade` records `expected_receiving_address`, `expected_amount` and `allowed_evidence_domains` before either party knows how a dispute will go. Evidence must be an https link on one of those domains.
- **Two-sided evidence.** Each party submits its own links. The snapshot freezes when both have submitted, or after the counterparty's grace period.
- **Deterministic checks in code.** `TRADE_COMPLETED` needs the expected address, and the expected amount when set, in the fetched evidence. If the evidence is a structured Blockscout transaction record, the contract checks recipient, exact amount, asset (native coin or the exact token contract recorded at creation), success status and time (between trade creation and the deadline) with integer arithmetic. One payment can settle only one trade. A trade can require that structured proof.
- **Deterministic consensus.** Validators re-run the fetch and the judgment and compare the decisive fields and the frozen evidence exactly in code (`gl.vm.run_nondet_unsafe`); there is no LLM comparator to disagree about wording.
- **No early escape.** Before the deadline only a proven completion can be recorded, so the side that has not performed cannot void the trade and recover its stake.
- **Frozen evidence.** Tier 1 stores the evidence content it judged and a root that commits to it. Validators must agree on that content, not just on the outcome. Tier 2 re-verifies the same frozen content and never re-fetches the web.
- **Independent Tier 2.** After the jury votes, validators derive their own outcome from the frozen evidence without seeing the Tier-1 reasoning or the jury's candidate. The majority stands only if they agree with it.
- **Fair jury.** The juror pool and weights are fixed as of trade acceptance (no late registration or stake top-ups) and frozen at appeal. Commits are bound to the juror and the case, so a copied commit can never be revealed. The jury is drawn after the appeal window from a drand round that did not exist when the appeal was filed, using integer-only weighted selection that anyone can replay with `verify_jury_selection`.
- **Never stuck.** `expire_unevidenced`, `expire_unresolved` and `expire_stuck_appeal` let anyone close a stalled trade; every path pays out or refunds, with pull-payment withdrawals.

See `ARCHITECTURE.md` for the full design, hardening notes and disclosed limitations.

## Lifecycle

```
created -> open (accepted) -> evidence_locked -> tier1_resolved
   -> [appeal window] -> finalized (no appeal)
                            |
                            v (appeal + bond, within window)
                       appeal_filed -> (window closes) draw_jury
                            -> appealed (commit -> reveal) -> finalized
```

`cancelled` (before acceptance) and the expiry methods above are the other exits.

## Outcomes

`TRADE_COMPLETED` / `PARTIALLY_COMPLETED` / `TRADE_BREACHED` / `UNDETERMINED`

## Contract

- `contract.py`: the full, documented source.
- `contract_deploy.py`: the same logic with comments and docstrings stripped via AST/tokenize (not regex). Deploy this one to GenLayer Studio; a heavily commented file can fail Studio's schema check.
- `contract_testnet_deploy.py`: the same logic with short windows, for quick jury testing.

## Tests

146 offline unit tests against a self-written `genlayer` SDK stub (`tests/genlayer_stub/`), no `pip install` required:

```
python3 -m unittest discover -s tests -v
```

They cover the full lifecycle, evidence rules, structured transaction proof, the anchor backstop, frozen evidence, jury selection and replay, commit-reveal, slashing, recovery paths and the clock. Setting `TA_CONTRACT=/path/to/contract_deploy.py` runs the same suite against the stripped deploy file.

## Live deployment

Production variant (full-length windows), GenLayer Studio: `0x8D4BEF845e2aE4E64062E112532DE7A3001d1B54`
(https://explorer-studio.genlayer.com/address/0x8D4BEF845e2aE4E64062E112532DE7A3001d1B54)

Short-window variant (`contract_testnet_deploy.py`, used for the jury path): `0xF47396Ec777FE19b0627B47Ba41A3AC2Fe679bf3`
(https://explorer-studio.genlayer.com/address/0xF47396Ec777FE19b0627B47Ba41A3AC2Fe679bf3)

The frontend is `index.html` (also served from GitHub Pages). `jury-console.html` is a helper page that drives five throwaway juror wallets (register, commit, reveal, withdraw) in parallel.

## Status

Contract and 146 offline tests complete and passing. Live runs on GenLayer Studio:

- Tier 1 on the production variant: `create_trade`, `accept_trade`, two-sided `submit_evidence`, automatic evidence lock and `resolve_tier1` reached consensus (Accepted, rotation count 0) with outcome TRADE_COMPLETED, backed by a real Base Sepolia payment verified through the structured Blockscout proof.
- Full jury path on the short-window variant, run twice with real Base Sepolia payments (0.0001 ETH):
  - `trade_0`: appeal, drand-seeded `draw_jury`, five jurors selected, two commits, no valid reveals, `finalize_jury` kept the Tier 1 outcome, non-reveal slashing paid to the appellant, pull-payment withdrawals reconciled to the wei.
  - `trade_1`: appeal, `draw_jury`, five commits, five reveals, majority verified against the frozen evidence (`verified: true`), final outcome TRADE_COMPLETED, forfeited appeal bond split evenly among the five majority jurors, and all balances withdrawn.
