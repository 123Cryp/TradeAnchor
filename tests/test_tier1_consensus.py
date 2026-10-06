import copy
import datetime
import hashlib
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    _contract_module as m, gl, make_contract, set_caller, reset_transfers, call_payable,
    submit_and_lock, PARTY_A_ADDRESS, PARTY_B_ADDRESS,
)
from test_jury_appeal import JuryTestBase

ADDRESS = "0x" + "aa" * 20
USDT = "0x" + "dd" * 20


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


def token_tx(ts=None, **over):
    tx = {
        "hash": "0xAbC1", "result": "success", "status": "ok", "timestamp": ts or future_iso(60),
        "value": "0", "to": {"hash": "0xTokenContract"}, "confirmations": 7,
        "token_transfers": [{"to": {"hash": ADDRESS}, "total": {"value": "250500000", "decimals": "6"},
                             "token": {"symbol": "USDT", "decimals": "6", "address_hash": USDT}}],
    }
    tx.update(over)
    return tx


def page_from(raw, url="https://example.com/tx"):
    with patch.object(gl.nondet.web, "render", side_effect=lambda u, mode="text": raw):
        return m._gather_pages([url], ADDRESS)[0]


def result_for(pages, **over):
    r = {
        "outcome": "TRADE_COMPLETED", "reasoning": "a", "anchor_match": True, "amount_match": True,
        "structured_verified": True, "pages_with_address": [True] * len(pages),
        "evidence_manifest": m._manifest(pages), "frozen": copy.deepcopy(pages),
    }
    r.update(over)
    return r


class Tier1Agreement(unittest.TestCase):
    def setUp(self):
        self.pages = [page_from(json.dumps(token_tx(ts="2030-01-01T00:00:00Z")))]

    def test_identical_agree_and_reasoning_may_differ(self):
        self.assertTrue(m._tier1_agree(result_for(self.pages), result_for(self.pages)))
        self.assertTrue(m._tier1_agree(result_for(self.pages, reasoning="x"), result_for(self.pages, reasoning="y")))

    def test_decisive_field_mismatch_rejected(self):
        for key, val in (("outcome", "UNDETERMINED"), ("anchor_match", False), ("amount_match", False),
                         ("structured_verified", False), ("pages_with_address", [False])):
            self.assertFalse(m._tier1_agree(result_for(self.pages), result_for(self.pages, **{key: val})), key)

    def test_manifest_mismatch_rejected(self):
        other = copy.deepcopy(self.pages)
        other[0]["excerpt_sha256"] = "0" * 64
        self.assertFalse(m._tier1_agree(result_for(self.pages), result_for(other)))
        self.assertFalse(m._tier1_agree(result_for(self.pages), result_for([])))

    def test_volatile_explorer_fields_do_not_change_the_frozen_record(self):
        a = page_from(json.dumps(token_tx(ts="2030-01-01T00:00:00Z", confirmations=7)))
        b = page_from(json.dumps(token_tx(ts="2030-01-01T00:00:00Z", confirmations=9000)))
        self.assertEqual(m._manifest([a]), m._manifest([b]))
        self.assertNotEqual(a["full_content_sha256"], b["full_content_sha256"])
        self.assertEqual(a["content"], json.dumps(a["tx"], sort_keys=True))

    def test_leader_cannot_store_frozen_content_its_manifest_does_not_describe(self):
        forged = result_for(self.pages)
        forged["frozen"][0]["content"] = "injected text"
        self.assertFalse(m._tier1_agree(forged, result_for(self.pages)))
        forged = result_for(self.pages)
        forged["frozen"][0]["tx"] = None
        self.assertFalse(m._tier1_agree(forged, result_for(self.pages)))
        forged = result_for(self.pages)
        forged["frozen"].append(copy.deepcopy(forged["frozen"][0]))
        self.assertFalse(m._tier1_agree(forged, result_for(self.pages)))
        forged = result_for(self.pages)
        forged["frozen"][0]["full_content_sha256"] = "not-a-hash"
        self.assertFalse(m._tier1_agree(forged, result_for(self.pages)))

    def test_non_dict_rejected(self):
        self.assertFalse(m._tier1_agree(None, result_for(self.pages)))

    def test_no_llm_comparator_in_source(self):
        src = open(m.__file__, encoding="utf-8").read()
        self.assertNotIn("prompt_comparative(", src)
        self.assertIn("run_nondet_unsafe", src)

    def test_excerpt_handles_characters_whose_lowercase_is_longer(self):
        text = "İ" * 50 + " paid to " + ADDRESS + " amount 5"
        self.assertIn(ADDRESS, m._excerpt(text, ADDRESS))


class PaymentWindowAndUnits(unittest.TestCase):
    def gate(self, tx, amount, not_before="", deadline="2031-01-01T00:00:00+00:00", addr=ADDRESS, token=None):
        page = {"url": "u", "content": "", "tx": m._extract_tx(json.dumps(tx))}
        if token is None:
            token = USDT if tx.get("token_transfers") else ""
        return m._structured_gate([page], addr, amount, deadline, not_before, token)

    def test_transfer_before_the_trade_was_created_does_not_count(self):
        tx = token_tx(ts="2026-01-01T00:00:00Z")
        self.assertEqual(self.gate(tx, "250.5 USDT", not_before="2026-06-01T00:00:00+00:00"), (True, False))
        self.assertEqual(self.gate(tx, "250.5 USDT", not_before="2025-06-01T00:00:00+00:00"), (True, True))

    def test_wei_and_gwei_amounts(self):
        native = {"hash": "0x1", "status": "ok", "result": "success", "timestamp": "2030-01-01T00:00:00Z",
                  "value": "31337", "to": {"hash": ADDRESS}, "token_transfers": []}
        self.assertEqual(self.gate(native, "31337 wei"), (True, True))
        self.assertEqual(self.gate(native, "31338 wei"), (True, False))
        self.assertEqual(self.gate(native, "0.000000000000031337 ETH"), (True, True))
        gw = dict(native, value=str(5 * 10 ** 9))
        self.assertEqual(self.gate(gw, "5 gwei"), (True, True))
        self.assertEqual(self.gate(gw, "5 Gwei"), (True, True))
        self.assertEqual(self.gate(token_tx(ts="2030-01-01T00:00:00Z"), "250500000 wei"), (True, False))

    def test_a_token_that_copies_the_symbol_is_rejected(self):
        tx = token_tx(ts="2030-01-01T00:00:00Z")
        self.assertEqual(self.gate(tx, "250.5 USDT", token=USDT), (True, True))
        self.assertEqual(self.gate(tx, "250.5 USDT", token="0x" + "ee" * 20), (True, False))
        self.assertEqual(self.gate(tx, "250.5 USDT", token=""), (True, False))
        self.assertEqual(self.gate(tx, "250.5", token=""), (True, False))
        no_addr = copy.deepcopy(tx)
        del no_addr["token_transfers"][0]["token"]["address_hash"]
        self.assertEqual(self.gate(no_addr, "250.5 USDT", token=""), (True, False))

    def test_hostile_transaction_json_does_not_crash_or_overflow(self):
        self.assertEqual(m._extract_tx(json.dumps(dict(token_tx(), token_transfers=5)))["transfers"], [])
        big = token_tx(token_transfers=[{"to": {"hash": ADDRESS}, "total": {"value": "9" * 4000, "decimals": "6"},
                                         "token": {"symbol": "X" * 500, "address_hash": USDT}}] * 50)
        tx = m._extract_tx(json.dumps(big))
        self.assertEqual(tx["transfers"], [])
        self.assertLessEqual(len(json.dumps(tx, sort_keys=True)), 6000)


class PaymentReplayAcrossTrades(unittest.TestCase):
    def make_trade(self, c):
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "250.5 USDT",
                           "example.com", future_iso(7200), 1, True, USDT)
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        submit_and_lock(c, tid, ["https://example.com/api/tx"])
        return tid

    def resolve(self, c, tid, raw):
        llm = {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "paid"}
        with patch.object(gl.nondet.web, "render", side_effect=lambda u, mode="text": raw), \
             patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
            c.resolve_tier1(tid)
        return json.loads(c.get_trade(tid))

    def test_one_payment_cannot_settle_two_trades(self):
        c = make_contract()
        reset_transfers()
        t1, t2 = self.make_trade(c), self.make_trade(c)
        raw = json.dumps(token_tx())
        first = self.resolve(c, t1, raw)
        self.assertEqual(first["tier1_outcome"], "TRADE_COMPLETED")
        self.assertEqual(first["payment_tx"], "0xabc1")
        with self.assertRaises(Exception):
            self.resolve(c, t2, raw)
        self.assertEqual(json.loads(c.get_trade(t2))["status"], "evidence_locked")
        self.assertEqual(c.payment_claims["0xabc1"], t1)
        # After the deadline the replayed payment still cannot complete it.
        tr = json.loads(c.trades[t2])
        tr["created_at"] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)).isoformat()
        tr["deadline"] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)).isoformat()
        c.trades[t2] = json.dumps(tr, sort_keys=True)
        second = self.resolve(c, t2, json.dumps(token_tx(ts=(datetime.datetime.now(datetime.timezone.utc)
                                                                - datetime.timedelta(hours=1)).isoformat())))
        self.assertEqual(second["tier1_outcome"], "UNDETERMINED")
        self.assertIn("already used", second["tier1_reasoning"])

    def test_a_different_payment_settles_the_second_trade(self):
        c = make_contract()
        reset_transfers()
        t1, t2 = self.make_trade(c), self.make_trade(c)
        self.resolve(c, t1, json.dumps(token_tx()))
        second = self.resolve(c, t2, json.dumps(token_tx(hash="0xdef2")))
        self.assertEqual(second["tier1_outcome"], "TRADE_COMPLETED")


class EvidenceTimingAndRecovery(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def open_trade(self, lead_seconds):
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(self.c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "5 ETH",
                           "example.com", future_iso(lead_seconds))
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_trade", 1000, tid)
        return tid

    def test_evidence_window_runs_past_a_far_deadline(self):
        tid = self.open_trade(20 * 24 * 3600)
        t = json.loads(self.c.get_trade(tid))
        gap = (datetime.datetime.fromisoformat(t["evidence_deadline"])
               - datetime.datetime.fromisoformat(t["deadline"])).total_seconds()
        self.assertEqual(gap, 3 * 24 * 3600)

    def test_one_side_cannot_lock_before_the_deadline(self):
        tid = self.open_trade(20 * 24 * 3600)
        set_caller(PARTY_A_ADDRESS)
        self.c.submit_evidence(tid, ["https://example.com/a"])
        t = json.loads(self.c.get_trade(tid))
        self.assertGreater(datetime.datetime.fromisoformat(t["evidence_lock_at"]),
                           datetime.datetime.fromisoformat(t["deadline"]))
        with self.assertRaises(Exception):
            self.c.lock_evidence(tid)

    def test_expire_unevidenced_locks_submitted_evidence_instead_of_discarding_it(self):
        tid = self.open_trade(7200)
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(tid, ["https://example.com/b"])
        tr = json.loads(self.c.trades[tid])
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)).isoformat()
        tr["evidence_deadline"] = past
        tr["evidence_lock_at"] = past
        self.c.trades[tid] = json.dumps(tr, sort_keys=True)
        self.c.expire_unevidenced(tid)
        t = json.loads(self.c.get_trade(tid))
        self.assertEqual(t["status"], "evidence_locked")
        self.assertIsNone(t["final_outcome"])

    def test_expire_unresolved_waits_for_the_trade_deadline(self):
        tid = self.open_trade(20 * 24 * 3600)
        set_caller(PARTY_A_ADDRESS)
        self.c.submit_evidence(tid, ["https://example.com/a"])
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(tid, ["https://example.com/b"])
        tr = json.loads(self.c.trades[tid])
        tr["evidence_snapshot"]["locked_at"] = (datetime.datetime.now(datetime.timezone.utc)
                                                - datetime.timedelta(days=8)).isoformat()
        self.c.trades[tid] = json.dumps(tr, sort_keys=True)
        with self.assertRaises(Exception):
            self.c.expire_unresolved(tid)

    def test_deadline_is_stored_in_utc(self):
        local = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3, minutes=30)))
                 + datetime.timedelta(hours=5)).isoformat()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(self.c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "5 ETH",
                           "example.com", local)
        self.assertTrue(json.loads(self.c.get_trade(tid))["deadline"].endswith("+00:00"))

    def test_count_eligible_jurors_before_acceptance_is_zero(self):
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(self.c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "5 ETH",
                           "example.com", future_iso(7200))
        self.assertEqual(self.c.count_eligible_jurors(tid), 0)


class TextProofIsNotVetoedByAnUnrelatedRecord(unittest.TestCase):
    def test_unrelated_transaction_record_does_not_block_a_text_proof(self):
        trade = {"expected_receiving_address": ADDRESS, "expected_amount": "5 ETH", "deadline": "2031-01-01T00:00:00+00:00",
                 "created_at": "2020-01-01T00:00:00+00:00", "require_structured_proof": False}
        text_page = {"url": "a", "content": "sent 5 ETH to " + ADDRESS, "tx": None}
        unrelated = {"url": "b", "content": "", "tx": m._extract_tx(json.dumps(dict(token_tx(), status="error")))}
        self.assertEqual(m._completion_gate([text_page, unrelated], trade), (True, False))
        trade["require_structured_proof"] = True
        self.assertEqual(m._completion_gate([text_page, unrelated], trade), (False, False))


class CommitBinding(JuryTestBase):
    def setUp(self):
        super().setUp()
        self.trade = self.do_appeal()
        self.selected = self.trade["jury"]["selected_jurors"]

    def test_a_copied_commit_cannot_be_revealed_by_the_copier(self):
        honest, copier = self.selected[0], self.selected[1]
        h = hashlib.sha256(f"TRADE_COMPLETED:s:{honest.lower()}:{self.trade_id}".encode()).hexdigest()
        set_caller(honest)
        self.c.commit_vote(self.trade_id, h)
        set_caller(copier)
        self.c.commit_vote(self.trade_id, h)
        tr = json.loads(self.c.trades[self.trade_id])
        tr["jury"]["commit_deadline"] = (datetime.datetime.now(datetime.timezone.utc)
                                         - datetime.timedelta(seconds=1)).isoformat()
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        set_caller(honest)
        self.c.reveal_vote(self.trade_id, "TRADE_COMPLETED", "s")
        set_caller(copier)
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.trade_id, "TRADE_COMPLETED", "s")


class NonRevealSlashIsNeverStranded(JuryTestBase):
    def setUp(self):
        super().setUp()
        self.trade = self.do_appeal()
        self.selected = self.trade["jury"]["selected_jurors"]

    def _finish(self):
        tr = json.loads(self.c.trades[self.trade_id])
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)).isoformat()
        tr["jury"]["commit_deadline"] = past
        tr["jury"]["reveal_deadline"] = past
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        self.c.finalize_jury(self.trade_id)

    def _stakes(self):
        return {a.lower(): int(json.loads(self.c.get_juror(a))["stake"]) for a in self.selected}

    def _pending(self, addr):
        return int(self.c.get_pending_withdrawal(addr))

    def test_nobody_revealed_slash_goes_to_the_appellant(self):
        before = self._stakes()
        bond = int(self.trade["appeal"]["bond_amount"])
        self._finish()
        slashed = sum(before.values()) - sum(self._stakes().values())
        self.assertGreater(slashed, 0)
        self.assertEqual(self._pending(PARTY_A_ADDRESS), bond + slashed + self._settled_to(PARTY_A_ADDRESS))

    def _settled_to(self, addr):
        t = json.loads(self.c.get_trade(self.trade_id))
        stake = int(t["stake_amount"])
        o = t["final_outcome"]
        if o == "TRADE_BREACHED":
            return 2 * stake if addr == t["party_a"] else 0
        if o == "TRADE_COMPLETED":
            return 2 * stake if addr == t["party_b"] else 0
        return stake

    def test_minority_of_revealers_split_the_non_reveal_slash(self):
        revealers = self.selected[:2]
        for i, addr in enumerate(revealers):
            set_caller(addr)
            h = hashlib.sha256(f"TRADE_COMPLETED:s{i}:{addr.lower()}:{self.trade_id}".encode()).hexdigest()
            self.c.commit_vote(self.trade_id, h)
        tr = json.loads(self.c.trades[self.trade_id])
        tr["jury"]["commit_deadline"] = (datetime.datetime.now(datetime.timezone.utc)
                                         - datetime.timedelta(seconds=1)).isoformat()
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        for i, addr in enumerate(revealers):
            set_caller(addr)
            self.c.reveal_vote(self.trade_id, "TRADE_COMPLETED", f"s{i}")
        before = self._stakes()
        self._finish()
        slashed = sum(before.values()) - sum(self._stakes().values())
        self.assertGreater(slashed, 0)
        self.assertEqual(sum(self._pending(a) for a in revealers), slashed)


class TokenContractRules(unittest.TestCase):
    def create(self, amount, require, token):
        c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        return call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, amount,
                            "example.com", future_iso(7200), 1, require, token)

    def test_structured_token_trade_needs_its_contract(self):
        with self.assertRaises(Exception):
            self.create("250.5 USDT", True, "")
        self.assertTrue(self.create("250.5 USDT", True, USDT))

    def test_native_amount_cannot_name_a_token_contract(self):
        with self.assertRaises(Exception):
            self.create("1 ETH", True, USDT)
        with self.assertRaises(Exception):
            self.create("31337 wei", False, USDT)
        self.assertTrue(self.create("1 ETH", True, ""))

    def test_amount_must_be_a_number_and_one_symbol(self):
        for bad in ("abc 250 USDT", "250 USDT or 300 DAI", "about 5 ETH", "5 ETH!", "-5 ETH"):
            with self.assertRaises(Exception, msg=bad):
                self.create(bad, False, "")
        for good in ("250.5", "1,250 USDT", "31337 wei", "0.5ETH"):
            self.assertTrue(self.create(good, False, USDT if "USDT" in good else ""), good)

    def test_token_contract_must_be_an_evm_address(self):
        for bad in ("usdt", "0x1234", "0x" + "g" * 40, "0x" + "dd" * 21):
            with self.assertRaises(Exception, msg=bad):
                self.create("250.5 USDT", True, bad)
        self.assertTrue(self.create("250.5 USDT", True, USDT.upper().replace("0X", "0x")))

    def test_text_mode_non_evm_asset_needs_no_contract(self):
        self.assertTrue(self.create("1 BTC", False, ""))


class TextProofSurvivesAnUnrelatedRecordEndToEnd(unittest.TestCase):
    def test_completion_proven_in_text_is_recorded(self):
        c = make_contract()
        reset_transfers()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "5 ETH",
                           "example.com", future_iso(7200))
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        c.submit_evidence(tid, ["https://example.com/page"])
        set_caller(PARTY_A_ADDRESS)
        c.submit_evidence(tid, ["https://example.com/api/unrelated"])
        pages = {"https://example.com/page": "sent 5 ETH to " + ADDRESS,
                 "https://example.com/api/unrelated": json.dumps(dict(token_tx(), status="error"))}
        llm = {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "paid"}
        with patch.object(gl.nondet.web, "render", side_effect=lambda u, mode="text": pages[u]), \
             patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
            c.resolve_tier1(tid)
        t = json.loads(c.get_trade(tid))
        self.assertEqual(t["tier1_outcome"], "TRADE_COMPLETED")
        self.assertNotIn("payment_tx", t)


class JurorWeightIsFixedAtAcceptance(JuryTestBase):
    def test_topping_up_after_acceptance_makes_a_juror_ineligible(self):
        before = self.c.count_eligible_jurors(self.trade_id)
        set_caller(self.jurors[0])
        call_payable(self.c, "register_juror", 10 ** 9)
        self.assertEqual(self.c.count_eligible_jurors(self.trade_id), before - 1)

    def test_pool_snapshot_is_capped_to_the_heaviest_jurors(self):
        self.c.MAX_JURY_POOL = 5
        set_caller(PARTY_A_ADDRESS)
        call_payable(self.c, "appeal", 200, self.trade_id)
        pool = json.loads(self.c.get_trade(self.trade_id))["appeal"]["eligible_pool"]
        self.assertEqual(len(pool), 5)
        lightest = min(self.jurors, key=lambda a: int(json.loads(self.c.get_juror(a))["stake"]))
        self.assertNotIn(lightest.lower(), [a.lower() for a, _ in pool])
        self.assertEqual(int(json.loads(self.c.get_juror(lightest)).get("pool_holds", 0)), 0)


class Tier2UsesTheFreezeTime(JuryTestBase):
    def test_breach_majority_on_evidence_frozen_before_the_deadline_is_not_verified(self):
        trade = json.loads(self.c.trades[self.trade_id])
        self.assertEqual(trade["tier1_outcome"], "TRADE_BREACHED")
        trade["tier1_resolved_at"] = (datetime.datetime.fromisoformat(trade["deadline"])
                                      - datetime.timedelta(hours=1)).isoformat()
        with patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"outcome": "TRADE_BREACHED"}):
            self.assertFalse(self.c._verify_majority_against_evidence(trade, "TRADE_BREACHED"))


if __name__ == "__main__":
    unittest.main()
