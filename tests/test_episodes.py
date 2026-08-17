from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atm_core.episodes import EconomicEpisode, EpisodeState, EpisodeStore, MAX_PROBE_EPISODES, MUTABLE_CLAIM_POLICY, PORTFOLIO_TOP_K, ProgressPolicy
from atm_core.execution_jobs import ProgressReceipt, utcnow_iso


def receipt(seq: int, *, progress: bool, error: str | None = None, evidence: bool = False) -> ProgressReceipt:
    before = f"{seq:064x}"
    after = f"{seq + 1:064x}" if progress else before
    return ProgressReceipt(
        execution_job_id="exec-test",
        checkpoint_seq=seq,
        objective_hash="a" * 64,
        artifact_before_hash=before,
        artifact_after_hash=after,
        tests_run=[],
        new_evidence_refs=[f"evidence:{seq}"] if evidence else [],
        blocker_class="NONE" if progress else "RECOVERABLE",
        uncertainty_or_acceptance_delta="improved" if progress else "",
        recommendation="CONTINUE",
        error_signature=error,
        created_at=utcnow_iso(),
    )


class EpisodeTests(unittest.TestCase):
    def test_policy_replan_recovery_kill(self):
        self.assertEqual(ProgressPolicy.decide([receipt(1, progress=False)]).action, "REPLAN")
        self.assertEqual(ProgressPolicy.decide([receipt(1, progress=False), receipt(2, progress=False)]).action, "RECOVERY")
        self.assertEqual(ProgressPolicy.decide([receipt(1, progress=False), receipt(2, progress=False), receipt(3, progress=False)]).action, "KILL")

    def test_same_error_three_times_without_new_evidence_kills(self):
        rows = [receipt(i, progress=True, error="E_TIMEOUT") for i in (1, 2, 3)]
        self.assertEqual(ProgressPolicy.decide(rows).reason, "SAME_ERROR_3X_NO_NEW_EVIDENCE")
        rows[-1] = receipt(3, progress=True, error="E_TIMEOUT", evidence=True)
        self.assertNotEqual(ProgressPolicy.decide(rows).reason, "SAME_ERROR_3X_NO_NEW_EVIDENCE")

    def test_recovery_uses_last_measurable_checkpoint(self):
        rows = [receipt(1, progress=True), receipt(2, progress=False), receipt(3, progress=False)]
        self.assertEqual(ProgressPolicy.recovery_checkpoint(rows).checkpoint_seq, 1)

    def test_top_k_and_probe_limit_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EpisodeStore(Path(directory) / "episodes.sqlite3")
            episodes = []
            for i in range(7):
                episodes.append(store.create_option(
                    canonical_opportunity_id=f"source:{i}", source="source", opportunity_type="code",
                    acceptance_contract_hash=f"{i:064x}", funding_evidence_refs=[f"funding:{i}"],
                    max_time_budget_seconds=3600, kill_condition="no progress", book="CASHFLOW_BOOK"
                ))
            self.assertEqual(len(store.top_options(99)), PORTFOLIO_TOP_K)
            for episode in episodes[:MAX_PROBE_EPISODES]:
                store.begin_probe(episode.episode_id)
            with self.assertRaisesRegex(ValueError, "max probe"):
                store.begin_probe(episodes[MAX_PROBE_EPISODES].episode_id)
            store.close()

    def test_probe_does_not_create_claim_and_claim_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EpisodeStore(Path(directory) / "episodes.sqlite3")
            episode = store.create_option(
                canonical_opportunity_id="work:1", source="work", opportunity_type="qa",
                acceptance_contract_hash="a" * 64, funding_evidence_refs=["proof"],
                max_time_budget_seconds=1800, kill_condition="timeout", book="CASHFLOW_BOOK"
            )
            probed = store.begin_probe(episode.episode_id)
            self.assertEqual(probed.economic_state, EpisodeState.PROBING)
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM mutable_claims").fetchone()[0], 0)
            committed = store.commit_mutable_claim(episode.episode_id)
            self.assertEqual(committed.economic_state, EpisodeState.COMMITTED)
            with self.assertRaisesRegex(ValueError, "claim-eligible|already"):
                store.commit_mutable_claim(episode.episode_id)
            store.close()

    def test_option_values_never_enter_realized_money(self):
        episode = EconomicEpisode(
            episode_id="ep", canonical_opportunity_id="x", source="x", opportunity_type="high_ticket",
            acceptance_contract_hash="f" * 64, max_time_budget_seconds=7200, kill_condition="stop",
            book="OPTION_BOOK", option_value={"reputation": 999999, "follow_on": 999999}
        )
        self.assertEqual(EpisodeStore.realized_money_contribution(episode), 0)
        self.assertEqual(MUTABLE_CLAIM_POLICY, "ONE_NORMAL_MUTABLE_CLAIM_LANE")


if __name__ == "__main__":
    unittest.main()
