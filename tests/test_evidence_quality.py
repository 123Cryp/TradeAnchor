import datetime
import json
import unittest

from _bootstrap import (
    make_contract, set_caller, reset_transfers, call_payable,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS,
)


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


class TestMinimumEvidenceSources(unittest.TestCase):
    """
    Covers the gap flagged in a Steward review of an adjacent sports-oracle
    project (MatchGuard): evidence quality must be enforceable, duplicate
    URLs must not be able to satisfy a source-count requirement, and the
    accepted source count must be persisted for audit. TradeAnchor has no
    multi-round challenge flow (so the duplicate-round-exhaustion and
    cumulative-counter-evidence failure modes don't apply here - see the
    chat record for why), but the underlying "is the evidence actually
    corroborated" concern is real and is what these tests cover.
    """

    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def _create(self, min_sources=1):
        set_caller(PARTY_A_ADDRESS)
        trade_id = call_payable(
            self.c, "create_trade", 1000,
            PARTY_B_ADDRESS, "OTC swap requiring independent corroboration",
            "At least the configured number of independent sources must agree",
            "0x" + "aa" * 20, "500",
            future_iso(7200), min_sources,
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_trade", 1000, trade_id)
        return trade_id

    def test_default_minimum_is_one_source(self):
        trade_id = self._create()  # default min_evidence_sources
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(trade_id, ["https://example.com/only-source"])
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["status"], "evidence_locked")
        self.assertEqual(trade["evidence_snapshot"]["accepted_source_count"], 1)

    def test_below_minimum_sources_is_rejected(self):
        trade_id = self._create(min_sources=3)
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(Exception):
            self.c.submit_evidence(trade_id, ["https://a.example.com", "https://b.example.com"])
        # and the trade must remain open, not partially locked
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["status"], "open")
        self.assertIsNone(trade["evidence_snapshot"])

    def test_duplicate_urls_are_deduplicated_before_counting(self):
        trade_id = self._create(min_sources=2)
        set_caller(PARTY_B_ADDRESS)
        # 3 raw URLs but only 1 distinct one -> must still fail the min=2 bar
        with self.assertRaises(Exception):
            self.c.submit_evidence(
                trade_id,
                ["https://a.example.com", "https://a.example.com", "https://a.example.com "],  # trailing space variant
            )
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertIsNone(trade["evidence_snapshot"])

    def test_meeting_minimum_with_duplicates_present_locks_with_correct_count(self):
        trade_id = self._create(min_sources=2)
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(
            trade_id,
            ["https://a.example.com", "https://a.example.com", "https://b.example.com"],
        )
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["status"], "evidence_locked")
        # accepted_source_count reflects the DEDUPLICATED count, not the raw input length
        self.assertEqual(trade["evidence_snapshot"]["accepted_source_count"], 2)
        self.assertEqual(len(trade["evidence_snapshot"]["source_urls"]), 2)

    def test_zero_min_evidence_sources_is_rejected_at_creation(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(Exception):
            call_payable(
                self.c, "create_trade", 1000,
                PARTY_B_ADDRESS, "obj", "criteria", "0x" + "aa" * 20, "", future_iso(7200), 0,
            )


if __name__ == "__main__":
    unittest.main()
