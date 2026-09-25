import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    make_contract, set_caller, reset_transfers, call_payable, gl,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS,
)

EXPECTED_ADDRESS = "0x" + "aa" * 20


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


class TestOnChainEvidenceAnchoring(unittest.TestCase):
    """
    Covers TradeAnchor's actual differentiator from Veridict: a
    TRADE_COMPLETED outcome must be backed by the on-chain-anchored
    expected_receiving_address actually showing up in the fetched
    evidence. This must hold even if the LLM itself claims a match
    that isn't textually grounded, or forgets to claim one at all -
    the code-level backstop in resolve_tier1 must not simply trust
    whatever the model asserts.
    """

    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def _create_and_lock(self, expected_amount="1 ETH"):
        set_caller(PARTY_A_ADDRESS)
        trade_id = call_payable(
            self.c, "create_trade", 100,
            PARTY_B_ADDRESS, "Send 1 ETH to the agreed receiving address",
            "Full amount must arrive at the recorded receiving address",
            EXPECTED_ADDRESS, expected_amount,
            future_iso(7200),
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_trade", 100, trade_id)
        self.c.submit_evidence(trade_id, ["https://explorer.example.com/tx/abc"])
        return trade_id

    def test_llm_claiming_completed_without_confirming_anchor_is_downgraded(self):
        """Model says TRADE_COMPLETED but never confirms anchor_match -
        must be forced to UNDETERMINED regardless of the claimed outcome."""
        trade_id = self._create_and_lock()
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "an unrelated page with no address"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"outcome": "TRADE_COMPLETED", "reasoning": "looks fine"}):
            self.c.resolve_tier1(trade_id)

        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["tier1_outcome"], "UNDETERMINED")

    def test_llm_explicitly_denying_anchor_match_is_downgraded(self):
        """Model explicitly returns anchor_match: false alongside a
        TRADE_COMPLETED claim - still forced to UNDETERMINED."""
        trade_id = self._create_and_lock()
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "some evidence text"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {
                              "outcome": "TRADE_COMPLETED", "anchor_match": False, "reasoning": "address not found"
                          }):
            self.c.resolve_tier1(trade_id)

        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["tier1_outcome"], "UNDETERMINED")

    def test_confirmed_anchor_match_allows_trade_completed_to_stand(self):
        """When the model DOES confirm anchor_match, TRADE_COMPLETED is
        allowed through unmodified - the backstop must not be so strict
        it blocks the legitimate case."""
        trade_id = self._create_and_lock()
        with patch.object(gl.nondet.web, "render",
                           side_effect=lambda url, mode="text": f"tx sent to {EXPECTED_ADDRESS} for 1 ETH, confirmed"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {
                              "outcome": "TRADE_COMPLETED", "anchor_match": True, "reasoning": "address and amount match"
                          }):
            self.c.resolve_tier1(trade_id)

        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["tier1_outcome"], "TRADE_COMPLETED")
        self.assertTrue(trade["tier1_anchor_match"])

    def test_anchor_backstop_does_not_affect_non_completed_outcomes(self):
        """A TRADE_BREACHED or UNDETERMINED outcome must pass through
        untouched regardless of anchor_match - the backstop only ever
        downgrades a TRADE_COMPLETED claim, never other outcomes."""
        trade_id = self._create_and_lock()
        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "nothing sent"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {
                              "outcome": "TRADE_BREACHED", "anchor_match": False, "reasoning": "no funds moved"
                          }):
            self.c.resolve_tier1(trade_id)

        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["tier1_outcome"], "TRADE_BREACHED")

    def test_empty_expected_amount_is_allowed_at_creation(self):
        """expected_amount may be blank for a best-effort swap with no
        fixed amount to anchor - only expected_receiving_address is
        mandatory."""
        trade_id = self._create_and_lock(expected_amount="")
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["expected_amount"], "")
        self.assertEqual(trade["expected_receiving_address"], EXPECTED_ADDRESS)

    def test_empty_expected_receiving_address_is_rejected_at_creation(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(Exception):
            call_payable(
                self.c, "create_trade", 100,
                PARTY_B_ADDRESS, "terms", "criteria", "   ", "1 ETH",
                future_iso(7200),
            )


if __name__ == "__main__":
    unittest.main()
