import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    make_contract, set_caller, reset_transfers, call_payable, gl,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS, STRANGER_ADDRESS, JUROR_ADDRESSES,
)

ADDRESS = "0x" + "aa" * 20


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


def past_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)).isoformat()


def patch_trade(c, trade_id, **fields):
    t = json.loads(c.trades[trade_id])
    t.update(fields)
    c.trades[trade_id] = json.dumps(t, sort_keys=True)


class TestTwoPartyEvidence(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()
        set_caller(PARTY_A_ADDRESS)
        self.trade_id = call_payable(
            self.c, "create_trade", 1000, PARTY_B_ADDRESS, "terms", "criteria",
            ADDRESS, "", "example.com", future_iso(7200), 1,
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_trade", 1000, self.trade_id)

    def _trade(self):
        return json.loads(self.c.get_trade(self.trade_id))

    def test_first_submission_does_not_freeze_the_snapshot(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.submit_evidence(self.trade_id, ["https://example.com/junk"])
        t = self._trade()
        self.assertEqual(t["status"], "open")
        self.assertIsNone(t["evidence_snapshot"])
        self.assertIsNotNone(t["evidence_lock_at"])

    def test_counterparty_can_still_add_evidence_and_both_are_merged(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.submit_evidence(self.trade_id, ["https://example.com/junk"])
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(self.trade_id, ["https://example.com/real-tx"])
        t = self._trade()
        self.assertEqual(t["status"], "evidence_locked")
        self.assertEqual(
            t["evidence_snapshot"]["source_urls"],
            ["https://example.com/junk", "https://example.com/real-tx"],
        )
        self.assertEqual(len(t["evidence_snapshot"]["submitted_by"]), 2)

    def test_lock_before_grace_period_is_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.submit_evidence(self.trade_id, ["https://example.com/junk"])
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(Exception):
            self.c.lock_evidence(self.trade_id)

    def test_anyone_can_lock_after_grace_and_counterparty_is_then_shut_out(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.submit_evidence(self.trade_id, ["https://example.com/a"])
        patch_trade(self.c, self.trade_id, evidence_lock_at=past_iso(5))
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(Exception):
            self.c.submit_evidence(self.trade_id, ["https://example.com/b"])
        set_caller(STRANGER_ADDRESS)
        self.c.lock_evidence(self.trade_id)
        self.assertEqual(self._trade()["status"], "evidence_locked")

    def test_lock_without_any_submission_is_rejected(self):
        with self.assertRaises(Exception):
            self.c.lock_evidence(self.trade_id)

    def test_a_party_cannot_submit_twice(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.submit_evidence(self.trade_id, ["https://example.com/a"])
        with self.assertRaises(Exception):
            self.c.submit_evidence(self.trade_id, ["https://example.com/a2"])

    def test_too_many_urls_and_overlong_urls_are_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(Exception):
            self.c.submit_evidence(self.trade_id, [f"https://example.com/{i}" for i in range(6)])
        with self.assertRaises(Exception):
            self.c.submit_evidence(self.trade_id, ["https://example.com/" + "x" * 400])

    def test_min_sources_counts_the_merged_set(self):
        c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "",
                           "example.com", future_iso(7200), 2)
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        set_caller(PARTY_A_ADDRESS)
        c.submit_evidence(tid, ["https://a.example.com/a"])
        set_caller(PARTY_B_ADDRESS)
        c.submit_evidence(tid, ["https://b.example.com/b"])
        self.assertEqual(json.loads(c.get_trade(tid))["status"], "evidence_locked")
        self.assertEqual(json.loads(c.get_trade(tid))["evidence_snapshot"]["accepted_source_count"], 2)

    def test_urls_on_one_host_do_not_count_as_independent_sources(self):
        c = make_contract()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "",
                           "example.com", future_iso(7200), 2)
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 1000, tid)
        set_caller(PARTY_A_ADDRESS)
        c.submit_evidence(tid, ["https://example.com/a", "https://example.com/b", "https://example.com/c"])
        set_caller(PARTY_B_ADDRESS)
        c.submit_evidence(tid, ["https://example.com/d"])
        self.assertEqual(json.loads(c.get_trade(tid))["status"], "open")


class TestDeadlineAndLimits(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def test_trade_cannot_be_accepted_after_its_deadline(self):
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(self.c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "",
                           "example.com", future_iso(7200))
        patch_trade(self.c, tid, deadline=past_iso(5))
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(Exception):
            call_payable(self.c, "accept_trade", 1000, tid)

    def test_oversized_text_and_domain_lists_are_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(Exception):
            call_payable(self.c, "create_trade", 1000, PARTY_B_ADDRESS, "t" * 2500, "c", ADDRESS, "",
                         "example.com", future_iso(7200))
        many = ",".join(f"d{i}.example.com" for i in range(11))
        with self.assertRaises(Exception):
            call_payable(self.c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "",
                         many, future_iso(7200))
        with self.assertRaises(Exception):
            call_payable(self.c, "create_trade", 1000, PARTY_B_ADDRESS, "t", "c", ADDRESS, "",
                         "example.com", future_iso(7200), 11)


class TestJurySelectionAtRealStakes(unittest.TestCase):
    """With stakes of 1 GEN (1e18 wei) the old u ** (1/weight) key
    saturated to 1.0 for every juror, so selection collapsed to a fixed
    order independent of the beacon. The log-form key must keep
    selection beacon-dependent at realistic stake sizes."""

    def _selected_for(self, beacon):
        c = make_contract()
        reset_transfers()
        set_caller(PARTY_A_ADDRESS)
        tid = call_payable(c, "create_trade", 10 ** 18, PARTY_B_ADDRESS, "t", "c", ADDRESS, "",
                           "example.com", future_iso(7200))
        set_caller(PARTY_B_ADDRESS)
        call_payable(c, "accept_trade", 10 ** 18, tid)
        from _bootstrap import submit_and_lock
        submit_and_lock(c, tid, ["https://example.com/x"])
        from _bootstrap import pass_deadline, age_jurors
        pass_deadline(c, tid)
        with patch.object(gl.nondet.web, "render", return_value="no address"), \
             patch.object(gl.nondet, "exec_prompt", return_value={"outcome": "TRADE_BREACHED", "reasoning": "x"}):
            c.resolve_tier1(tid)
        for a in JUROR_ADDRESSES:
            set_caller(a)
            call_payable(c, "register_juror", 10 ** 18)
        age_jurors(c)
        set_caller(PARTY_A_ADDRESS)
        call_payable(c, "appeal", 2 * 10 ** 17, tid)
        from _bootstrap import draw_jury
        draw_jury(c, tid, beacon)
        t = json.loads(c.get_trade(tid))
        check = json.loads(c.verify_jury_selection(tid))
        self.assertTrue(check["match"])
        return tuple(sorted(a.lower() for a in t["jury"]["selected_jurors"]))

    def test_selection_depends_on_the_beacon(self):
        results = {self._selected_for(f"beacon-{i}") for i in range(6)}
        self.assertGreater(len(results), 1)


if __name__ == "__main__":
    unittest.main()
