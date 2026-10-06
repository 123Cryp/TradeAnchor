import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    draw_jury, set_frozen,
    make_contract, set_caller, reset_transfers, call_payable, gl, submit_and_lock,
    pass_deadline, age_jurors,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS, STRANGER_ADDRESS, JUROR_ADDRESSES,
)
from test_jury_appeal import JuryTestBase

ADDRESS = "0x" + "aa" * 20


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


def past_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)).isoformat()


def locked_trade(c, stake=1000):
    set_caller(PARTY_A_ADDRESS)
    tid = call_payable(c, "create_trade", stake, PARTY_B_ADDRESS, "t", "c", ADDRESS, "",
                       "example.com", future_iso(7200))
    set_caller(PARTY_B_ADDRESS)
    call_payable(c, "accept_trade", stake, tid)
    submit_and_lock(c, tid, ["https://example.com/x"])
    return tid


def tweak(c, tid, **fields):
    t = json.loads(c.trades[tid])
    t.update(fields)
    c.trades[tid] = json.dumps(t, sort_keys=True)


class TestEarlyResolutionAndParsing(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def _resolve(self, tid, llm, page="no address"):
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": page), \
             patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
            self.c.resolve_tier1(tid)
        return json.loads(self.c.get_trade(tid))

    def test_completed_without_expected_amount_in_text_is_undetermined(self):
        c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "250 USDT",
                           "example.com", future_iso(7200))
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        submit_and_lock(c, tid, ["https://example.com/x"])
        pass_deadline(c, tid)
        llm = {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"}
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "sent 10 to " + ADDRESS), \
             patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
            c.resolve_tier1(tid)
        self.assertEqual(json.loads(c.get_trade(tid))["tier1_outcome"], "UNDETERMINED")

    def test_completed_with_expected_amount_in_text_stands(self):
        c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "1,250 USDT",
                           "example.com", future_iso(7200))
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        submit_and_lock(c, tid, ["https://example.com/x"])
        llm = {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"}
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "sent 1250.00 USDT to " + ADDRESS), \
             patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
            c.resolve_tier1(tid)
        self.assertEqual(json.loads(c.get_trade(tid))["tier1_outcome"], "TRADE_COMPLETED")

    def test_amount_far_from_address_or_inside_a_longer_number_does_not_count(self):
        for page in ("sent 1000250 to " + ADDRESS,
                     "amount 1250 " + "x" * 600 + " to " + ADDRESS):
            c = make_contract()
            set_caller(PARTY_A_ADDRESS)
            tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "1250 USDT",
                               "example.com", future_iso(7200))
            set_caller(PARTY_B_ADDRESS)
            call_payable(c, "accept_trade", 1000, tid)
            submit_and_lock(c, tid, ["https://example.com/x"])
            pass_deadline(c, tid)
            llm = {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"}
            with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text", p=page: p), \
                 patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
                c.resolve_tier1(tid)
            self.assertEqual(json.loads(c.get_trade(tid))["tier1_outcome"], "UNDETERMINED")

    def test_breach_before_deadline_is_premature(self):
        tid = locked_trade(self.c)
        with self.assertRaises(Exception):
            self._resolve(tid, {"outcome": "TRADE_BREACHED", "anchor_match": False, "reasoning": "x"})
        t = json.loads(self.c.get_trade(tid))
        self.assertEqual((t["status"], t["tier1_outcome"]), ("evidence_locked", None))
        pass_deadline(self.c, tid)
        t = self._resolve(tid, {"outcome": "TRADE_BREACHED", "anchor_match": False, "reasoning": "x"})
        self.assertEqual(t["tier1_outcome"], "TRADE_BREACHED")

    def test_partial_before_deadline_is_premature(self):
        tid = locked_trade(self.c)
        with self.assertRaises(Exception):
            self._resolve(tid, {"outcome": "PARTIALLY_COMPLETED", "anchor_match": True, "reasoning": "x"})
        self.assertEqual(json.loads(self.c.get_trade(tid))["status"], "evidence_locked")

    def test_undetermined_before_deadline_is_not_recorded(self):
        tid = locked_trade(self.c)
        with self.assertRaises(Exception):
            self._resolve(tid, {"outcome": "UNDETERMINED", "anchor_match": False, "reasoning": "x"})
        self.assertEqual(json.loads(self.c.get_trade(tid))["status"], "evidence_locked")

    def test_confirmed_completion_may_resolve_before_deadline(self):
        tid = locked_trade(self.c)
        t = self._resolve(tid, {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"},
                          page="sent to " + ADDRESS)
        self.assertEqual(t["tier1_outcome"], "TRADE_COMPLETED")

    def test_breach_after_deadline_stands(self):
        tid = locked_trade(self.c)
        pass_deadline(self.c, tid)
        t = self._resolve(tid, {"outcome": "TRADE_BREACHED", "anchor_match": False, "reasoning": "x"})
        self.assertEqual(t["tier1_outcome"], "TRADE_BREACHED")

    def test_unparsable_model_output_becomes_undetermined_not_a_crash(self):
        tid = locked_trade(self.c)
        pass_deadline(self.c, tid)
        t = self._resolve(tid, "this is not json at all")
        self.assertEqual(t["tier1_outcome"], "UNDETERMINED")


class TestStuckTier1Recovery(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def test_cannot_expire_before_timeout(self):
        tid = locked_trade(self.c)
        with self.assertRaises(Exception):
            self.c.expire_unresolved(tid)

    def test_expire_after_timeout_refunds_both(self):
        tid = locked_trade(self.c, 1000)
        t = json.loads(self.c.get_trade(tid))
        t["evidence_snapshot"]["locked_at"] = past_iso(8 * 24 * 3600)
        t["deadline"] = past_iso(8 * 24 * 3600)
        self.c.trades[tid] = json.dumps(t, sort_keys=True)
        set_caller(STRANGER_ADDRESS)
        self.c.expire_unresolved(tid)
        t = json.loads(self.c.get_trade(tid))
        self.assertEqual((t["status"], t["final_outcome"]), ("finalized", "UNDETERMINED"))
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), 1000)
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), 1000)


class TestTier2IsGrounded(JuryTestBase):
    def setUp(self):
        super().setUp()
        self.trade = self.do_appeal()
        self.selected = self.trade["jury"]["selected_jurors"]

    def _vote_all(self, vote):
        import hashlib
        for i, addr in enumerate(self.selected):
            set_caller(addr)
            h = hashlib.sha256(f"{vote}:s{i}:{addr.lower()}:{self.trade_id}".encode()).hexdigest()
            self.c.commit_vote(self.trade_id, h)
        tr = json.loads(self.c.trades[self.trade_id])
        tr["jury"]["commit_deadline"] = past_iso(2)
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        for i, addr in enumerate(self.selected):
            set_caller(addr)
            self.c.reveal_vote(self.trade_id, vote, f"s{i}")
        tr = json.loads(self.c.trades[self.trade_id])
        tr["jury"]["reveal_deadline"] = past_iso(1)
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)

    def test_completed_majority_without_address_in_text_fails_verification(self):
        self._vote_all("TRADE_COMPLETED")
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "no address in this page"), \
             patch.object(gl.nondet, "exec_prompt", return_value={"outcome": "TRADE_COMPLETED"}):
            self.c.finalize_jury(self.trade_id)
        t = json.loads(self.c.get_trade(self.trade_id))
        self.assertFalse(t["jury"]["verification_passed"])
        self.assertEqual(t["final_outcome"], "TRADE_BREACHED")

    def test_verification_judges_the_frozen_content_and_never_refetches(self):
        self._vote_all("TRADE_BREACHED")
        set_frozen(self.c, self.trade_id, "FROZEN-TEXT-777")
        seen = []

        def fake_prompt(p, response_format="json"):
            seen.append(p)
            return {"outcome": "TRADE_BREACHED"}

        def no_fetch(url, mode="text"):
            raise AssertionError("Tier 2 must not fetch live pages")

        with patch.object(gl.nondet.web, "render", side_effect=no_fetch), \
             patch.object(gl.nondet, "exec_prompt", side_effect=fake_prompt):
            self.c.finalize_jury(self.trade_id)
        self.assertTrue(any("FROZEN-TEXT-777" in p for p in seen))
        self.assertFalse(any("ORIGINAL AUTOMATED REASONING" in p or "CANDIDATE OUTCOME" in p for p in seen))
        t = json.loads(self.c.get_trade(self.trade_id))
        self.assertTrue(t["jury"]["verification_passed"])

    def test_tampered_frozen_content_fails_verification(self):
        self._vote_all("TRADE_BREACHED")
        set_frozen(self.c, self.trade_id, "original text")
        tr = json.loads(self.c.trades[self.trade_id])
        tr["evidence_content"][0]["content"] = "edited text"
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        with patch.object(gl.nondet, "exec_prompt", return_value={"outcome": "TRADE_BREACHED"}):
            self.c.finalize_jury(self.trade_id)
        t = json.loads(self.c.get_trade(self.trade_id))
        self.assertFalse(t["jury"]["verification_passed"])

    def test_disagreeing_independent_outcome_fails_verification(self):
        self._vote_all("TRADE_BREACHED")
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "x"), \
             patch.object(gl.nondet, "exec_prompt", return_value={"outcome": "UNDETERMINED"}):
            self.c.finalize_jury(self.trade_id)
        t = json.loads(self.c.get_trade(self.trade_id))
        self.assertFalse(t["jury"]["verification_passed"])


class TestStuckAppealRecovery(JuryTestBase):
    def setUp(self):
        super().setUp()
        self.trade = self.do_appeal()
        self.selected = self.trade["jury"]["selected_jurors"]

    def test_cannot_recover_before_timeout(self):
        with self.assertRaises(Exception):
            self.c.expire_stuck_appeal(self.trade_id)

    def test_recovery_holds_tier1_refunds_bond_and_unlocks_jurors(self):
        tr = json.loads(self.c.trades[self.trade_id])
        old = past_iso(9 * 24 * 3600)
        tr["jury"]["commit_deadline"] = old
        tr["jury"]["reveal_deadline"] = old
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        set_caller(STRANGER_ADDRESS)
        self.c.expire_stuck_appeal(self.trade_id)
        t = json.loads(self.c.get_trade(self.trade_id))
        self.assertEqual(t["status"], "finalized")
        self.assertEqual(t["final_outcome"], t["tier1_outcome"])
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)) >= 200, True)
        for a in self.selected:
            self.assertEqual(int(json.loads(self.c.get_juror(a))["locked_cases"]), 0)


class TestJurorPoolSnapshot(JuryTestBase):
    def test_jurors_registered_after_acceptance_are_not_eligible(self):
        # The base class aged the first six jurors. Register a seventh now.
        late = JUROR_ADDRESSES[6]
        set_caller(late)
        call_payable(self.c, "register_juror", 10 ** 12)
        trade = self.do_appeal()
        self.assertNotIn(late.lower(), [a.lower() for a in trade["jury"]["selected_jurors"]])

    def test_appeal_fails_if_only_late_jurors_exist(self):
        c2 = make_contract()
        tid = locked_trade(c2)
        pass_deadline(c2, tid)
        with patch.object(gl.nondet.web, "render", return_value="x"), \
             patch.object(gl.nondet, "exec_prompt", return_value={"outcome": "TRADE_BREACHED", "reasoning": "x"}):
            c2.resolve_tier1(tid)
        for a in JUROR_ADDRESSES[:6]:
            set_caller(a)
            call_payable(c2, "register_juror", 1000)
        set_caller(PARTY_A_ADDRESS)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "x"}'):
            with self.assertRaises(Exception):
                call_payable(c2, "appeal", 200, tid)


if __name__ == "__main__":
    unittest.main()



class TestFrozenEvidenceContent(unittest.TestCase):
    def test_tier1_stores_hash_and_excerpt_of_what_it_judged(self):
        c = make_contract()
        reset_transfers()
        tid = locked_trade(c)
        page = "header " + "y" * 3000 + " paid to " + ADDRESS + " done"
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": page), \
             patch.object(gl.nondet, "exec_prompt",
                          return_value={"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"}):
            c.resolve_tier1(tid)
        t = json.loads(c.get_trade(tid))
        self.assertEqual(len(t["evidence_content"]), 1)
        frozen = t["evidence_content"][0]
        import hashlib
        self.assertIn(ADDRESS, frozen["content"])
        import hashlib as _h
        self.assertEqual(frozen["excerpt_sha256"], _h.sha256(frozen["content"].encode()).hexdigest())
        self.assertEqual(t["evidence_content_root"], __import__("_bootstrap")._contract_module._evidence_root(t["evidence_content"]))


class TestDelayedJuryDraw(JuryTestBase):
    def _appeal_only(self):
        set_caller(PARTY_A_ADDRESS)
        call_payable(self.c, "appeal", 200, self.trade_id)
        return json.loads(self.c.get_trade(self.trade_id))

    def test_appeal_does_not_pick_a_jury_yet(self):
        t = self._appeal_only()
        self.assertEqual(t["status"], "appeal_filed")
        self.assertIsNone(t.get("jury"))
        set_caller(self.jurors[0])
        with self.assertRaises(Exception):
            self.c.commit_vote(self.trade_id, "0" * 64)

    def test_draw_is_blocked_until_the_round_is_published(self):
        self._appeal_only()
        with patch.object(gl.nondet.web, "render", return_value='{"round": 1, "randomness": "x"}'):
            with self.assertRaises(Exception):
                self.c.draw_jury(self.trade_id)

    def test_draw_rejects_a_beacon_for_a_different_round(self):
        self._appeal_only()
        tr = json.loads(self.c.trades[self.trade_id])
        now = datetime.datetime.now(datetime.timezone.utc)
        tr["appeal"]["draw_round"] = int((now.timestamp() - 240 - 1595431050) // 30 + 1)
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        with patch.object(gl.nondet.web, "render", return_value='{"round": 5, "randomness": "x"}'):
            with self.assertRaises(Exception):
                self.c.draw_jury(self.trade_id)

    def test_replay_detects_a_tampered_jury(self):
        self._appeal_only()
        draw_jury(self.c, self.trade_id)
        self.assertTrue(json.loads(self.c.verify_jury_selection(self.trade_id))["match"])
        tr = json.loads(self.c.trades[self.trade_id])
        outsider = [a for a in self.jurors if a not in tr["jury"]["selected_jurors"]]
        tr["jury"]["selected_jurors"][0] = outsider[0] if outsider else "0x" + "ee" * 20
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        self.assertFalse(json.loads(self.c.verify_jury_selection(self.trade_id))["match"])

    def test_filed_appeal_that_is_never_drawn_can_be_recovered(self):
        self._appeal_only()
        with self.assertRaises(Exception):
            self.c.expire_stuck_appeal(self.trade_id)
        tr = json.loads(self.c.trades[self.trade_id])
        tr["appeal_deadline"] = past_iso(9 * 24 * 3600)
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        set_caller(STRANGER_ADDRESS)
        self.c.expire_stuck_appeal(self.trade_id)
        t = json.loads(self.c.get_trade(self.trade_id))
        self.assertEqual((t["status"], t["final_outcome"]), ("finalized", t["tier1_outcome"]))
        self.assertGreaterEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), 200)

    def test_no_second_appeal_and_no_unappealed_finalize_once_filed(self):
        self._appeal_only()
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(Exception):
            call_payable(self.c, "appeal", 200, self.trade_id)
        with self.assertRaises(Exception):
            self.c.finalize_unappealed(self.trade_id)


class TestPoolSnapshotAndCommitFormat(JuryTestBase):
    def test_pool_is_frozen_at_appeal_and_late_unstake_drops_out(self):
        set_caller(PARTY_A_ADDRESS)
        call_payable(self.c, "appeal", 200, self.trade_id)
        t = json.loads(self.c.get_trade(self.trade_id))
        snap = [a for a, _ in t["appeal"]["eligible_pool"]]
        self.assertEqual(len(snap), 6)
        # a juror registered after the appeal is not in the snapshot
        late = JUROR_ADDRESSES[7]
        set_caller(late)
        call_payable(self.c, "register_juror", 5000)
        draw_jury(self.c, self.trade_id)
        j = json.loads(self.c.get_trade(self.trade_id))["jury"]
        self.assertNotIn(late.lower(), [a.lower() for a, _ in j["pool"]])
        self.assertTrue(json.loads(self.c.verify_jury_selection(self.trade_id))["match"])

    def test_commit_hash_must_be_sha256_hex(self):
        trade = self.do_appeal()
        juror = trade["jury"]["selected_jurors"][0]
        set_caller(juror)
        for bad in ("abc", "z" * 64, "0" * 63):
            with self.assertRaises(Exception):
                self.c.commit_vote(self.trade_id, bad)
        self.c.commit_vote(self.trade_id, "A" * 64)
        self.assertEqual(json.loads(self.c.get_trade(self.trade_id))["jury"]["commits"][juror.lower()], "a" * 64)


class TestEvidenceManifest(unittest.TestCase):
    def test_excerpt_is_stable_when_only_text_far_from_the_address_changes(self):
        from _bootstrap import _contract_module as m
        a = "updated 3 minutes ago " + "z" * 2000 + " to " + ADDRESS + " value 5 " + "q" * 2000 + " 12 confirmations"
        b = "updated 9 minutes ago " + "z" * 2000 + " to " + ADDRESS + " value 5 " + "q" * 2000 + " 13 confirmations"
        self.assertEqual(m._excerpt(a, ADDRESS), m._excerpt(b, ADDRESS))

    def test_excerpt_changes_when_the_text_around_the_address_changes(self):
        from _bootstrap import _contract_module as m
        self.assertNotEqual(m._excerpt("to " + ADDRESS + " value 5", ADDRESS), m._excerpt("to " + ADDRESS + " value 6", ADDRESS))

    def test_whitespace_differences_do_not_change_the_excerpt(self):
        from _bootstrap import _contract_module as m
        self.assertEqual(m._excerpt("to   " + ADDRESS + "\n value 5", ADDRESS), m._excerpt("to " + ADDRESS + " value 5", ADDRESS))

    def test_page_without_the_address_keeps_only_a_short_head(self):
        from _bootstrap import _contract_module as m
        self.assertEqual(len(m._excerpt("x" * 5000, ADDRESS)), 1500)

    def test_root_commits_to_the_manifest_and_the_full_page_hashes(self):
        from _bootstrap import _contract_module as m
        import hashlib as _h
        page = {"url": "u", "content": "c", "excerpt_sha256": _h.sha256(b"c").hexdigest(),
                "full_content_sha256": _h.sha256(b"whole page").hexdigest(), "tx": None}
        root = m._evidence_root([page])
        self.assertTrue(m._frozen_intact([page], root))
        # the manifest validators compare never contains the volatile whole-page hash
        self.assertNotIn(page["full_content_sha256"], json.dumps(m._manifest([page])))
        # but changing it breaks the commitment (tamper evidence)
        self.assertNotEqual(root, m._evidence_root([dict(page, full_content_sha256="0" * 64)]))
        # editing stored content is detected
        self.assertFalse(m._frozen_intact([dict(page, content="edited")], root))

    def test_frozen_record_fields_match_the_manifest_exactly(self):
        c = make_contract()
        reset_transfers()
        tid = locked_trade(c)
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "paid to " + ADDRESS), \
             patch.object(gl.nondet, "exec_prompt",
                          return_value={"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"}):
            c.resolve_tier1(tid)
        t = json.loads(c.get_trade(tid))
        from _bootstrap import _contract_module as m
        import hashlib as _h
        rec = t["evidence_content"][0]
        self.assertEqual(rec["full_content_sha256"], _h.sha256(("paid to " + ADDRESS).encode()).hexdigest())
        self.assertEqual(rec["excerpt_sha256"], _h.sha256(rec["content"].encode()).hexdigest())
        self.assertEqual(t["evidence_content_root"], m._evidence_root(t["evidence_content"]))
        self.assertTrue(m._frozen_intact(t["evidence_content"], t["evidence_content_root"]))


REAL_TX = {
    "hash": "0x5c504ed432cb51138bcf09aa5e8a410dd4a1e204ef84bfed1be16dfba1b22060",
    "result": "success", "status": "ok", "timestamp": "2015-08-07T03:30:33.000000Z",
    "value": "31337",
    "from": {"hash": "0xA1E4380A3B1f749673E270229993eE55F35663b4"},
    "to": {"hash": "0x5DF9B87991262F6BA471F09758CDE1c0FC1De734"},
    "token_transfers": [],
}
USDT = "0x" + "dd" * 20
TOKEN_TX = {
    "hash": "0xabc", "result": "success", "status": "ok", "timestamp": "2026-01-01T00:00:00.000000Z",
    "value": "0", "to": {"hash": "0xTokenContract"},
    "token_transfers": [{"to": {"hash": ADDRESS}, "total": {"value": "250500000", "decimals": "6"},
                         "token": {"symbol": "USDT", "decimals": "6", "address_hash": USDT}}],
}


class TestStructuredTransactionProof(unittest.TestCase):
    def _gate(self, tx, amount, addr=ADDRESS, deadline="2030-01-01T00:00:00+00:00"):
        from _bootstrap import _contract_module as m
        page = {"url": "u", "content": "", "tx": m._extract_tx(json.dumps(tx))}
        token = USDT if tx.get("token_transfers") else ""
        return m._structured_gate([page], addr, amount, deadline, "", token)

    def test_native_transfer_matches_exact_amount_and_recipient(self):
        to = REAL_TX["to"]["hash"]
        self.assertEqual(self._gate(REAL_TX, "0.000000000000031337 ETH", to), (True, True))
        self.assertEqual(self._gate(REAL_TX, "0.000000000000031338 ETH", to), (True, False))

    def test_wrong_recipient_fails(self):
        self.assertEqual(self._gate(REAL_TX, "0.000000000000031337", ADDRESS), (True, False))

    def test_token_transfer_needs_amount_and_symbol(self):
        self.assertEqual(self._gate(TOKEN_TX, "250.5 USDT"), (True, True))
        self.assertEqual(self._gate(TOKEN_TX, "250.5 DAI"), (True, False))
        self.assertEqual(self._gate(TOKEN_TX, "250 USDT"), (True, False))

    def test_failed_or_late_transaction_fails(self):
        bad = dict(TOKEN_TX, status="error", result="failed")
        self.assertEqual(self._gate(bad, "250.5 USDT"), (True, False))
        self.assertEqual(self._gate(TOKEN_TX, "250.5 USDT", deadline="2025-01-01T00:00:00+00:00"), (True, False))

    def test_non_json_pages_are_not_structured(self):
        from _bootstrap import _contract_module as m
        self.assertIsNone(m._extract_tx("<html>to 0x.. 5</html>"))
        self.assertEqual(m._structured_gate([{"url": "u", "content": "x", "tx": None}], ADDRESS, "5", "2030-01-01T00:00:00+00:00"), (False, False))

    def test_resolution_uses_the_structured_record_over_text_proximity(self):
        c = make_contract()
        reset_transfers()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "250.5 USDT",
                           "example.com", future_iso(7200), 1, False, USDT)
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        submit_and_lock(c, tid, ["https://example.com/api/tx"])
        llm = {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"}
        padded = dict(TOKEN_TX, filler="x" * 5000, timestamp=future_iso(60))  # address and amount far apart in the text
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": json.dumps(padded)), \
             patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
            c.resolve_tier1(tid)
        t = json.loads(c.get_trade(tid))
        self.assertEqual(t["tier1_outcome"], "TRADE_COMPLETED")
        self.assertTrue(t["evidence_content"][0]["tx"]["ok"])

    def test_resolution_rejects_a_structured_record_with_the_wrong_amount(self):
        c = make_contract()
        reset_transfers()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "999 USDT",
                           "example.com", future_iso(7200))
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        submit_and_lock(c, tid, ["https://example.com/api/tx"])
        pass_deadline(c, tid)
        llm = {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"}
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": json.dumps(TOKEN_TX)), \
             patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
            c.resolve_tier1(tid)
        self.assertEqual(json.loads(c.get_trade(tid))["tier1_outcome"], "UNDETERMINED")


class TestPoolStakeHold(JuryTestBase):
    def test_pool_members_cannot_unstake_between_appeal_and_draw(self):
        set_caller(PARTY_A_ADDRESS)
        call_payable(self.c, "appeal", 200, self.trade_id)
        for a in self.jurors:
            set_caller(a)
            with self.assertRaises(Exception):
                self.c.unstake_juror("1")
        draw_jury(self.c, self.trade_id)
        t = json.loads(self.c.get_trade(self.trade_id))
        selected = [a.lower() for a in t["jury"]["selected_jurors"]]
        for a in self.jurors:
            rec = json.loads(self.c.get_juror(a))
            self.assertEqual(int(rec.get("pool_holds", 0)), 0)
            self.assertEqual(int(rec["locked_cases"]), 1 if a.lower() in selected else 0)
        outsider = [a for a in self.jurors if a.lower() not in selected]
        set_caller(outsider[0])
        self.c.unstake_juror("1")

    def test_recovering_an_undrawn_appeal_releases_the_holds(self):
        set_caller(PARTY_A_ADDRESS)
        call_payable(self.c, "appeal", 200, self.trade_id)
        tr = json.loads(self.c.trades[self.trade_id])
        tr["appeal_deadline"] = past_iso(9 * 24 * 3600)
        self.c.trades[self.trade_id] = json.dumps(tr, sort_keys=True)
        set_caller(STRANGER_ADDRESS)
        self.c.expire_stuck_appeal(self.trade_id)
        for a in self.jurors:
            self.assertEqual(int(json.loads(self.c.get_juror(a)).get("pool_holds", 0)), 0)


class TestRequiredStructuredProofAndSymbolParsing(unittest.TestCase):
    def _trade(self, amount, require):
        c = make_contract()
        reset_transfers()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, amount,
                           "example.com", future_iso(7200), 1, require, USDT if "USDT" in amount else "")
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        submit_and_lock(c, tid, ["https://example.com/page"])
        return c, tid

    def _resolve(self, c, tid, page):
        llm = {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "x"}
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": page), \
             patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": llm):
            c.resolve_tier1(tid)
        return json.loads(c.get_trade(tid))["tier1_outcome"]

    def test_required_proof_disables_the_text_fallback(self):
        c, tid = self._trade("5 ETH", True)
        pass_deadline(c, tid)
        self.assertEqual(self._resolve(c, tid, "sent 5 ETH to " + ADDRESS), "UNDETERMINED")

    def test_without_the_flag_the_text_fallback_still_works(self):
        c, tid = self._trade("5 ETH", False)
        self.assertEqual(self._resolve(c, tid, "sent 5 ETH to " + ADDRESS), "TRADE_COMPLETED")

    def test_required_proof_accepts_a_matching_structured_record(self):
        c, tid = self._trade("250.5 USDT", True)
        self.assertEqual(self._resolve(c, tid, json.dumps(dict(TOKEN_TX, timestamp=future_iso(60)))), "TRADE_COMPLETED")

    def test_symbol_is_the_word_after_the_number_not_the_first_word(self):
        from _bootstrap import _contract_module as m
        page = {"url": "u", "content": "", "tx": m._extract_tx(json.dumps(TOKEN_TX))}
        deadline = "2030-01-01T00:00:00+00:00"
        self.assertEqual(m._structured_gate([page], ADDRESS, "exactly 250.5 USDT", deadline, "", USDT), (True, True))
        self.assertEqual(m._structured_gate([page], ADDRESS, "about 250.5 DAI", deadline, "", USDT), (True, False))


class TestClock(unittest.TestCase):
    def test_now_uses_the_transaction_timestamp_when_available(self):
        from _bootstrap import _contract_module as m
        with patch.object(gl, "message_raw", {"datetime": "2031-02-03T04:05:06.000Z"}, create=True):
            self.assertEqual(m.TradeAnchor._now_utc().isoformat(), "2031-02-03T04:05:06+00:00")

    def test_now_falls_back_when_the_timestamp_is_missing_or_malformed(self):
        from _bootstrap import _contract_module as m
        with patch.object(gl, "message_raw", {"datetime": "garbage"}, create=True):
            self.assertIsNotNone(m.TradeAnchor._now_utc().tzinfo)


class TestAmountParserRegression(unittest.TestCase):
    def _gate(self, amount):
        from _bootstrap import _contract_module as m
        page = {"url": "u", "content": "", "tx": m._extract_tx(json.dumps(TOKEN_TX))}
        return m._structured_gate([page], ADDRESS, amount, "2030-01-01T00:00:00+00:00", "", USDT)[1]

    def test_accepted_formats(self):
        for amount in ("250.5 USDT", "250.5 usdt", "250.5USDT", "250.500 USDT", "exactly 250.5 USDT",
                       "pay 250.5 USDT to the address", "250.5", "250.5 tokens", "250.5 units"):
            self.assertTrue(self._gate(amount), amount)

    def test_rejected_formats(self):
        for amount in ("250.6 USDT", "250 USDT", "2505 USDT", "250.5 DAI", "0.5 USDT", "1250.5 USDT"):
            self.assertFalse(self._gate(amount), amount)

    def test_thousands_separators_are_ignored(self):
        from _bootstrap import _contract_module as m
        tx = dict(TOKEN_TX, token_transfers=[{"to": {"hash": ADDRESS}, "total": {"value": "1250500000", "decimals": "6"},
                                              "token": {"symbol": "USDT", "address_hash": USDT}}])
        page = {"url": "u", "content": "", "tx": m._extract_tx(json.dumps(tx))}
        self.assertTrue(m._structured_gate([page], ADDRESS, "1,250.5 USDT", "2030-01-01T00:00:00+00:00", "", USDT)[1])


class TestClockCapturedOncePerCall(unittest.TestCase):
    def test_accept_trade_uses_one_timestamp_for_every_derived_field(self):
        c = make_contract()
        reset_transfers()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "",
                           "example.com", future_iso(7200))
        set_caller(PARTY_B_ADDRESS)
        ticks = iter(range(1000))
        base = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)
        calls = []

        def moving_clock():
            calls.append(1)
            return base + datetime.timedelta(seconds=next(ticks))

        with patch.object(type(c), "_now_utc", staticmethod(moving_clock)):
            call_payable(c, "accept_trade", 1000, tid)
        self.assertEqual(len(calls), 1)
        t = json.loads(c.get_trade(tid))
        self.assertEqual(t["accepted_at"], base.isoformat())


class TestNumericTimestampAndEligibleCount(JuryTestBase):
    def test_numeric_timestamps_in_seconds_and_milliseconds_are_understood(self):
        from _bootstrap import _contract_module as m
        for ts in (1767225600, 1767225600000, "1767225600"):
            tx = dict(TOKEN_TX, timestamp=ts)
            page = {"url": "u", "content": "", "tx": m._extract_tx(json.dumps(tx))}
            self.assertEqual(m._structured_gate([page], ADDRESS, "250.5 USDT", "2030-01-01T00:00:00+00:00", "", USDT), (True, True), ts)
            self.assertEqual(m._structured_gate([page], ADDRESS, "250.5 USDT", "2025-01-01T00:00:00+00:00", "", USDT), (True, False), ts)

    def test_count_eligible_jurors_matches_what_appeal_requires(self):
        self.assertEqual(self.c.count_eligible_jurors(self.trade_id), 6)
        set_caller(JUROR_ADDRESSES[7])
        call_payable(self.c, "register_juror", 5000)  # registered after acceptance
        self.assertEqual(self.c.count_eligible_jurors(self.trade_id), 6)
