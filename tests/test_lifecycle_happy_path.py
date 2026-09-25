import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    make_contract, set_caller, reset_transfers, call_payable,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS, gl,
)


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


class TestHappyPathFulfilled(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def test_full_lifecycle_fulfilled_pays_party_b(self):
        set_caller(PARTY_A_ADDRESS)
        trade_id = call_payable(
            self.c, "create_trade", 100,
            PARTY_B_ADDRESS, "Send 1 BTC to the agreed receiving address",
            "Full amount must arrive at the recorded receiving address",
            "bc1qxyz0000000000000000000000000000000000", "1 BTC",
            future_iso(7200),
        )

        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_trade", 100, trade_id)

        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(trade_id, ["https://example.com/explorer/tx123"])

        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "tx to bc1qxyz0000000000000000000000000000000000 for 1 BTC, confirmed"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "address and amount match"}):
            self.c.resolve_tier1(trade_id)

        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["status"], "tier1_resolved")
        self.assertEqual(trade["tier1_outcome"], "TRADE_COMPLETED")

        # simulate appeal window passing
        trade["appeal_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.trades[trade_id] = json.dumps(trade, sort_keys=True)

        self.c.finalize_unappealed(trade_id)

        self.assertEqual(self.c.get_pending_withdrawal(PARTY_B_ADDRESS), "200")
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "0")

        set_caller(PARTY_B_ADDRESS)
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers[-1]["to"], PARTY_B_ADDRESS)
        self.assertEqual(int(gl.evm.transfers[-1]["value"]), 200)

    def test_cannot_settle_twice(self):
        set_caller(PARTY_A_ADDRESS)
        trade_id = call_payable(
            self.c, "create_trade", 100,
            PARTY_B_ADDRESS, "Send 1 BTC to the agreed receiving address",
            "Full amount must arrive at the recorded receiving address",
            "bc1qxyz0000000000000000000000000000000000", "1 BTC",
            future_iso(7200),
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_trade", 100, trade_id)
        self.c.submit_evidence(trade_id, ["https://example.com/explorer/tx456"])

        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "tx to bc1qxyz0000000000000000000000000000000000 for 1 BTC, confirmed"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "ok"}):
            self.c.resolve_tier1(trade_id)

        trade = json.loads(self.c.get_trade(trade_id))
        trade["appeal_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.trades[trade_id] = json.dumps(trade, sort_keys=True)

        self.c.finalize_unappealed(trade_id)
        with self.assertRaises(Exception):
            self.c.finalize_unappealed(trade_id)  # already finalized -> wrong status
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_B_ADDRESS), "200")


if __name__ == "__main__":
    unittest.main()
