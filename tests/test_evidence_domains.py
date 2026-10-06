import datetime
import json
import unittest

from _bootstrap import (
    submit_and_lock,
    make_contract, set_caller, reset_transfers, call_payable,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS,
)

ADDRESS = "0x" + "aa" * 20


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


class TestEvidenceDomainAllowlist(unittest.TestCase):
    """
    The allowed evidence domains are recorded on-chain at creation and
    seen by party_b before accepting. submit_evidence must reject any URL
    outside that list, so a party cannot host its own page containing the
    expected address and submit it as evidence.
    """

    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def _open_trade(self, domains="etherscan.io, basescan.org"):
        set_caller(PARTY_A_ADDRESS)
        trade_id = call_payable(
            self.c, "create_trade", 100,
            PARTY_B_ADDRESS, "terms", "criteria", ADDRESS, "",
            domains, future_iso(7200),
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_trade", 100, trade_id)
        return trade_id

    def test_domains_are_normalized_and_stored_on_chain(self):
        trade_id = self._open_trade(" Etherscan.IO , basescan.org,, etherscan.io ")
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["allowed_evidence_domains"], ["etherscan.io", "basescan.org"])

    def test_allowed_domain_and_its_subdomain_are_accepted(self):
        trade_id = self._open_trade()
        submit_and_lock(self.c, trade_id, ["https://etherscan.io/tx/0xabc", "https://sepolia.etherscan.io/tx/0xabc"])
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["status"], "evidence_locked")

    def test_self_hosted_page_on_other_domain_is_rejected(self):
        trade_id = self._open_trade()
        with self.assertRaises(Exception):
            submit_and_lock(self.c, trade_id, ["https://raw.githubusercontent.com/me/x/main/fake.txt"])
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertEqual(trade["status"], "open")

    def test_lookalike_suffix_domain_is_rejected(self):
        trade_id = self._open_trade()
        with self.assertRaises(Exception):
            submit_and_lock(self.c, trade_id, ["https://notetherscan.io/tx/0xabc"])
        with self.assertRaises(Exception):
            submit_and_lock(self.c, trade_id, ["https://etherscan.io.evil.example/tx/0xabc"])

    def test_embedded_credentials_url_is_rejected(self):
        trade_id = self._open_trade()
        with self.assertRaises(Exception):
            submit_and_lock(self.c, trade_id, ["https://etherscan.io:443@attacker.example/"])
        with self.assertRaises(Exception):
            submit_and_lock(self.c, trade_id, ["https://user:pw@etherscan.io/tx/0xabc"])

    def test_non_https_scheme_is_rejected(self):
        trade_id = self._open_trade()
        with self.assertRaises(Exception):
            submit_and_lock(self.c, trade_id, ["http://etherscan.io/tx/0xabc"])
        with self.assertRaises(Exception):
            submit_and_lock(self.c, trade_id, ["ftp://etherscan.io/tx/0xabc"])

    def test_one_bad_url_among_good_ones_rejects_the_whole_submission(self):
        trade_id = self._open_trade()
        with self.assertRaises(Exception):
            submit_and_lock(self.c, trade_id, ["https://etherscan.io/tx/0xabc", "https://evil.example/x"])
        trade = json.loads(self.c.get_trade(trade_id))
        self.assertIsNone(trade["evidence_snapshot"])

    def test_empty_domain_list_is_rejected_at_creation(self):
        set_caller(PARTY_A_ADDRESS)
        for bad in ("", "  ,  "):
            with self.assertRaises(Exception):
                call_payable(
                    self.c, "create_trade", 100,
                    PARTY_B_ADDRESS, "terms", "criteria", ADDRESS, "",
                    bad, future_iso(7200),
                )

    def test_malformed_domain_entries_are_rejected_at_creation(self):
        set_caller(PARTY_A_ADDRESS)
        for bad in ("https://etherscan.io", "etherscan.io/tx", "user@etherscan.io", "localhost", "etherscan.io:443"):
            with self.assertRaises(Exception):
                call_payable(
                    self.c, "create_trade", 100,
                    PARTY_B_ADDRESS, "terms", "criteria", ADDRESS, "",
                    bad, future_iso(7200),
                )


if __name__ == "__main__":
    unittest.main()
