from __future__ import annotations

from app.ranking import RankSignals, Ranker


def test_rerank_score_uses_full_zero_to_three_scale() -> None:
    ranker = Ranker()

    assert ranker.rerank_score(
        RankSignals(
            authority=4,
            freshness=1.0,
            quality=1.0,
            relevance=1.0,
            click_prior=1.0,
            spam_penalty=0.0,
        )
    ) == 3.0
    assert ranker.rerank_score(
        RankSignals(
            authority=0,
            freshness=0.0,
            quality=0.0,
            relevance=0.0,
            click_prior=0.0,
            spam_penalty=0.0,
        )
    ) == 0.0


def test_rerank_score_worked_example_is_reachable() -> None:
    score = Ranker().rerank_score(
        RankSignals(
            authority=4,
            freshness=1.0,
            quality=0.9,
            relevance=1.0,
            click_prior=1.0,
            spam_penalty=0.0,
        )
    )

    assert score == 2.925
    assert score >= 1.5
