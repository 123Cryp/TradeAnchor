import datetime
import hashlib
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    make_contract, set_caller, reset_transfers, call_payable, gl,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS, STRANGER_ADDRESS, JUROR_ADDRESSES,
)


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


def commit_hash(vote, salt):
    return hashlib.sha256(f"{vote}:{salt}".encode()).hexdigest()


class JuryTestBase(unittest.TestCase):
    """Shared setup: an trade through Tier-1 resolution (TRADE_BREACHED),
    plus 6 staked jurors, ready for appeal."""

    def setUp(self):
        self.c = make_contract()
        reset_transfers()

        set_caller(PARTY_A_ADDRESS)
        self.trade_id = call_payable(
            self.c, "create_trade", 1000,
            PARTY_B_ADDRESS, "Send 2 ETH to the agreed receiving address",
            "Full amount must arrive at the recorded receiving address",
            "0x" + "aa" * 20, "2 ETH",
            future_iso(7200),
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_trade", 1000, self.trade_id)
        self.c.submit_evidence(self.trade_id, ["https://example.com/audit-report"])

        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "partial audit only"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"outcome": "TRADE_BREACHED", "reasoning": "missing files"}):
            self.c.resolve_tier1(self.trade_id)

        self.jurors = JUROR_ADDRESSES[:6]
        for i, addr in enumerate(self.jurors):
            set_caller(addr)
            call_payable(self.c, "register_juror", 1000 + i * 100)

    def do_appeal(self, appellant_address=PARTY_A_ADDRESS):
        set_caller(appellant_address)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "fixed-test-beacon"}'):
            call_payable(self.c, "appeal", 200, self.trade_id)  # 20% of 1000
        return json.loads(self.c.get_trade(self.trade_id))


class TestAppealAndJurySelection(JuryTestBase):
    def test_appeal_selects_jury_of_five_and_is_reproducible(self):
        trade = self.do_appeal()
        self.assertEqual(trade["status"], "appealed")
        jury = trade["jury"]
        self.assertEqual(len(jury["selected_jurors"]), 5)
        self.assertEqual(len(set(a.lower() for a in jury["selected_jurors"])), 5)

        check = json.loads(self.c.verify_jury_selection(self.trade_id))
        self.assertTrue(check["match"])

    def test_wrong_appeal_bond_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "x"}'):
            with self.assertRaises(Exception):
                call_payable(self.c, "appeal", 1, self.trade_id)

    def test_stranger_cannot_appeal(self):
        set_caller(STRANGER_ADDRESS)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "x"}'):
            with self.assertRaises(Exception):
                call_payable(self.c, "appeal", 200, self.trade_id)

    def test_appeal_fails_without_enough_staked_jurors(self):
        c2 = make_contract()
        set_caller(PARTY_A_ADDRESS)
        trade_id = call_payable(
            c2, "create_trade", 1000, PARTY_B_ADDRESS, "obj", "crit",
            "0x" + "aa" * 20, "", future_iso(7200)
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(c2, "accept_trade", 1000, trade_id)
        c2.submit_evidence(trade_id, ["https://example.com/x"])
        with patch.object(gl.nondet.web, "render", return_value="x"), \
             patch.object(gl.nondet, "exec_prompt", return_value={"outcome": "TRADE_BREACHED", "reasoning": "x"}):
            c2.resolve_tier1(trade_id)
        # only register 2 jurors -- not enough for JURY_SIZE=5
        for addr in JUROR_ADDRESSES[:2]:
            set_caller(addr)
            call_payable(c2, "register_juror", 500)
        set_caller(PARTY_A_ADDRESS)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "x"}'):
            with self.assertRaises(Exception):
                call_payable(c2, "appeal", 200, trade_id)


class TestCommitReveal(JuryTestBase):
    def setUp(self):
        super().setUp()
        self.trade = self.do_appeal()
        self.selected = self.trade["jury"]["selected_jurors"]

    def _open_reveal(self):
        trade = json.loads(self.c.get_trade(self.trade_id))
        trade["jury"]["commit_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.trades[self.trade_id] = json.dumps(trade, sort_keys=True)

    def test_only_selected_jurors_can_commit(self):
        non_juror = [a for a in JUROR_ADDRESSES if a.lower() not in [s.lower() for s in self.selected]][0]
        set_caller(non_juror)
        with self.assertRaises(Exception):
            self.c.commit_vote(self.trade_id, commit_hash("TRADE_BREACHED", "salt"))

    def test_reveal_must_match_commit(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.trade_id, commit_hash("TRADE_BREACHED", "saltA"))
        self._open_reveal()
        set_caller(juror)
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.trade_id, "TRADE_BREACHED", "wrong-salt")
        self.c.reveal_vote(self.trade_id, "TRADE_BREACHED", "saltA")

    def test_commit_cannot_be_changed(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.trade_id, commit_hash("TRADE_BREACHED", "s1"))
        with self.assertRaises(Exception):
            self.c.commit_vote(self.trade_id, commit_hash("TRADE_COMPLETED", "s2"))

    def test_cannot_reveal_before_commit_window_closes(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.trade_id, commit_hash("TRADE_BREACHED", "s1"))
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.trade_id, "TRADE_BREACHED", "s1")

    def test_cannot_replay_a_reveal(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.trade_id, commit_hash("TRADE_BREACHED", "s1"))
        self._open_reveal()
        set_caller(juror)
        self.c.reveal_vote(self.trade_id, "TRADE_BREACHED", "s1")
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.trade_id, "TRADE_BREACHED", "s1")


class TestFinalizeJury(JuryTestBase):
    def setUp(self):
        super().setUp()
        self.trade = self.do_appeal()
        self.selected = self.trade["jury"]["selected_jurors"]

    def _commit_and_reveal(self, votes: dict):
        """votes: {address: vote}; jurors not present in votes never commit."""
        salts = {addr: f"salt-{i}" for i, addr in enumerate(self.selected)}
        for addr in self.selected:
            vote = votes.get(addr.lower())
            if vote is not None:
                set_caller(addr)
                self.c.commit_vote(self.trade_id, commit_hash(vote, salts[addr]))

        trade = json.loads(self.c.get_trade(self.trade_id))
        trade["jury"]["commit_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.trades[self.trade_id] = json.dumps(trade, sort_keys=True)

        for addr in self.selected:
            vote = votes.get(addr.lower())
            if vote is not None:
                set_caller(addr)
                self.c.reveal_vote(self.trade_id, vote, salts[addr])

        trade = json.loads(self.c.get_trade(self.trade_id))
        trade["jury"]["reveal_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.trades[self.trade_id] = json.dumps(trade, sort_keys=True)

    def test_verified_majority_overturns_tier1_and_slashes_minority(self):
        votes = {addr.lower(): "TRADE_COMPLETED" for addr in self.selected[:4]}
        votes[self.selected[4].lower()] = "TRADE_BREACHED"
        self._commit_and_reveal(votes)

        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            self.c.finalize_jury(self.trade_id)

        final_agreement = json.loads(self.c.get_trade(self.trade_id))
        self.assertEqual(final_agreement["final_outcome"], "TRADE_COMPLETED")
        self.assertTrue(final_agreement["jury"]["verification_passed"])
        # tier1 was TRADE_BREACHED, tier2 outcome differs -> appellant (party_a) wins, bond returned
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "200")
        minority = json.loads(self.c.get_juror(self.selected[4]))
        self.assertEqual(minority["minority_votes"], 1)
        majority_addr = self.selected[0]
        self.assertGreater(int(self.c.get_pending_withdrawal(majority_addr)), 0)
        # party_b (winner of TRADE_COMPLETED outcome) got the pot
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_B_ADDRESS), "2000")

    def test_appellant_loses_appeal_forfeits_bond_to_reward_pool(self):
        # majority agrees WITH tier1 (TRADE_BREACHED) -> appellant loses, bond forfeited
        votes = {addr.lower(): "TRADE_BREACHED" for addr in self.selected}
        self._commit_and_reveal(votes)
        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            self.c.finalize_jury(self.trade_id)

        final_agreement = json.loads(self.c.get_trade(self.trade_id))
        self.assertEqual(final_agreement["final_outcome"], "TRADE_BREACHED")
        # party_a is BOTH the appellant (loses appeal, bond forfeited) AND the
        # TRADE_BREACHED-outcome winner (gets the full 2000 pot) -- bond forfeiture
        # only means the extra +200 bond never lands on top of that pot.
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "2000")
        # all 5 jurors were unanimous majority -> each gets a share of the forfeited 200 bond
        total_juror_rewards = sum(int(self.c.get_pending_withdrawal(a)) for a in self.selected)
        self.assertEqual(total_juror_rewards, 200)

    def test_unverified_majority_slashes_nobody_for_disagreeing_but_slashes_non_reveal(self):
        votes = {addr.lower(): "TRADE_COMPLETED" for addr in self.selected[:4]}  # 5th never reveals
        self._commit_and_reveal(votes)
        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": False}):
            self.c.finalize_jury(self.trade_id)

        final_agreement = json.loads(self.c.get_trade(self.trade_id))
        self.assertEqual(final_agreement["final_outcome"], final_agreement["tier1_outcome"])
        self.assertFalse(final_agreement["jury"]["verification_passed"])
        non_reveal_juror = json.loads(self.c.get_juror(self.selected[4]))
        self.assertEqual(non_reveal_juror["non_reveals"], 1)
        # outcome holds at tier1 (TRADE_BREACHED) -> party_a gets the 2000 pot as
        # the winning party, PLUS the 200 bond refunded (verification
        # failure is nobody's fault) = 2200 total.
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "2200")
        # the 4 who revealed TRADE_COMPLETED were NOT slashed for disagreeing with tier1
        for addr in self.selected[:4]:
            record = json.loads(self.c.get_juror(addr))
            self.assertEqual(record["minority_votes"], 0)

    def test_tie_falls_back_to_tier1_no_slashing(self):
        votes = {
            self.selected[0].lower(): "TRADE_COMPLETED",
            self.selected[1].lower(): "TRADE_COMPLETED",
            self.selected[2].lower(): "TRADE_BREACHED",
            self.selected[3].lower(): "TRADE_BREACHED",
            # 5th never reveals -> 2-2 tie among reveals, no majority
        }
        self._commit_and_reveal(votes)
        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            self.c.finalize_jury(self.trade_id)

        final_agreement = json.loads(self.c.get_trade(self.trade_id))
        self.assertEqual(final_agreement["final_outcome"], final_agreement["tier1_outcome"])
        self.assertIsNone(final_agreement["jury"]["provisional_outcome"])
        for addr in self.selected[:4]:
            record = json.loads(self.c.get_juror(addr))
            self.assertEqual(record["minority_votes"], 0)  # nobody slashed for a genuine tie

    def test_double_finalize_is_impossible(self):
        votes = {addr.lower(): "TRADE_BREACHED" for addr in self.selected}
        self._commit_and_reveal(votes)
        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            self.c.finalize_jury(self.trade_id)
        with self.assertRaises(Exception):
            self.c.finalize_jury(self.trade_id)  # already finalized -> wrong status


class TestReputationAndWeightCap(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_reputation_is_bounded(self):
        addr = JUROR_ADDRESSES[0]
        set_caller(addr)
        call_payable(self.c, "register_juror", 1000)
        record = self.c._load_juror(addr)
        for _ in range(200):
            record["reputation"] = self.c._clamp_reputation(record["reputation"] + 5)
        self.assertEqual(record["reputation"], self.c.REPUTATION_MAX)
        for _ in range(200):
            record["reputation"] = self.c._clamp_reputation(record["reputation"] - 5)
        self.assertEqual(record["reputation"], self.c.REPUTATION_MIN)

    def test_selection_weight_respects_stake_cap(self):
        whale = {"stake": str(10 ** 30), "reputation": 0}
        normal = {"stake": str(1000), "reputation": 0}
        w_whale = self.c._selection_weight(whale)
        w_normal = self.c._selection_weight(normal)
        # whale's weight is capped at MAX_EFFECTIVE_STAKE, not the raw stake
        self.assertEqual(w_whale, float(int(self.c.MAX_EFFECTIVE_STAKE)))
        self.assertLess(w_whale / w_normal, float(self.c.MAX_EFFECTIVE_STAKE))


if __name__ == "__main__":
    unittest.main()
