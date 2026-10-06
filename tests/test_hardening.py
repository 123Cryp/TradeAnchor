import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    pass_deadline, age_jurors,
    submit_and_lock,
    make_contract, set_caller, reset_transfers, call_payable, gl,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS, JUROR_ADDRESSES,
)

ADDRESS = "0x" + "aa" * 20


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


def make_open_trade(c, stake=1000):
    set_caller(PARTY_A_ADDRESS)
    trade_id = call_payable(
        c, "create_trade", stake, PARTY_B_ADDRESS, "terms", "criteria",
        ADDRESS, "", "example.com", future_iso(7200),
    )
    set_caller(PARTY_B_ADDRESS)
    call_payable(c, "accept_trade", stake, trade_id)
    return trade_id


def backdate(c, trade_id, field, seconds):
    t = json.loads(c.trades[trade_id])
    t[field] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)).isoformat()
    c.trades[trade_id] = json.dumps(t, sort_keys=True)


class TestCodeLevelAnchorCheck(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def _resolve(self, page_text):
        trade_id = make_open_trade(self.c)
        submit_and_lock(self.c, trade_id, ["https://example.com/tx/1"])
        pass_deadline(self.c, trade_id)
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": page_text), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {
                              "outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "ok"}):
            self.c.resolve_tier1(trade_id)
        return json.loads(self.c.get_trade(trade_id))

    def test_model_claiming_anchor_without_address_in_text_is_downgraded(self):
        trade = self._resolve("a page that never mentions the recipient")
        self.assertEqual(trade["tier1_outcome"], "UNDETERMINED")
        self.assertFalse(trade["tier1_anchor_match"])

    def test_address_in_text_is_matched_case_insensitively(self):
        trade = self._resolve("sent to " + ADDRESS.upper().replace("0X", "0x"))
        self.assertEqual(trade["tier1_outcome"], "TRADE_COMPLETED")
        self.assertTrue(trade["tier1_anchor_match"])


class TestExpireUnevidenced(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def test_cannot_expire_before_deadline(self):
        trade_id = make_open_trade(self.c)
        with self.assertRaises(Exception):
            self.c.expire_unevidenced(trade_id)

    def test_expire_refunds_both_stakes(self):
        trade_id = make_open_trade(self.c, 1000)
        backdate(self.c, trade_id, "evidence_deadline", 10)
        set_caller("0x" + "99" * 20)
        self.c.expire_unevidenced(trade_id)
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["status"], "finalized")
        self.assertEqual(trade["final_outcome"], "UNDETERMINED")
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)), 1000)
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), 1000)

    def test_cannot_expire_twice_or_after_evidence_locked(self):
        trade_id = make_open_trade(self.c)
        submit_and_lock(self.c, trade_id, ["https://example.com/x"])
        backdate(self.c, trade_id, "evidence_deadline", 10)
        with self.assertRaises(Exception):
            self.c.expire_unevidenced(trade_id)


class TestJuryHardening(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()
        self.trade_id = make_open_trade(self.c, 1000)
        submit_and_lock(self.c, self.trade_id, ["https://example.com/x"])
        pass_deadline(self.c, self.trade_id)
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "no address here"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"outcome": "TRADE_BREACHED", "reasoning": "x"}):
            self.c.resolve_tier1(self.trade_id)

    def _register(self, addresses, amount=1000):
        for a in addresses:
            set_caller(a)
            call_payable(self.c, "register_juror", amount)
        age_jurors(self.c)

    def _appeal(self):
        set_caller(PARTY_A_ADDRESS)
        call_payable(self.c, "appeal", 200, self.trade_id)
        from _bootstrap import draw_jury
        draw_jury(self.c, self.trade_id, "beacon")
        return json.loads(self.c.get_trade(self.trade_id))

    def test_parties_are_never_selected_as_jurors(self):
        self._register(JUROR_ADDRESSES[:5])
        self._register([PARTY_A_ADDRESS, PARTY_B_ADDRESS], 10 ** 9)
        trade = self._appeal()
        selected = [a.lower() for a in trade["jury"]["selected_jurors"]]
        self.assertNotIn(PARTY_A_ADDRESS.lower(), selected)
        self.assertNotIn(PARTY_B_ADDRESS.lower(), selected)

    def test_appeal_needs_five_non_party_jurors(self):
        self._register(JUROR_ADDRESSES[:4])
        self._register([PARTY_A_ADDRESS, PARTY_B_ADDRESS])
        with self.assertRaises(Exception):
            self._appeal()

    def test_selected_juror_cannot_unstake_until_jury_finalizes(self):
        self._register(JUROR_ADDRESSES[:5])
        trade = self._appeal()
        juror = trade["jury"]["selected_jurors"][0]
        set_caller(juror)
        with self.assertRaises(Exception):
            self.c.unstake_juror("500")

        backdate_jury = json.loads(self.c.trades[self.trade_id])
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)).isoformat()
        backdate_jury["jury"]["commit_deadline"] = past
        backdate_jury["jury"]["reveal_deadline"] = past
        self.c.trades[self.trade_id] = json.dumps(backdate_jury, sort_keys=True)
        set_caller(JUROR_ADDRESSES[0])
        self.c.finalize_jury(self.trade_id)

        rec = json.loads(self.c.get_juror(juror))
        self.assertEqual(int(rec["locked_cases"]), 0)
        set_caller(juror)
        self.c.unstake_juror("1")


if __name__ == "__main__":
    unittest.main()
