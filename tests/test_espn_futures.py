"""Award-futures odds: American-odds conversion and de-vigging.

De-vigging is the part that must be right. A 108-runner futures market carries
an enormous overround (measured live: 1.68), so raw implied probabilities would
overstate every player's ceiling by two-thirds.
"""
from __future__ import annotations

import pytest

from fantasyprep.sources.espn_futures import (
    FutureOdds,
    _normalize,
    american_to_probability,
    market_overround,
    parse_american,
    upside_by_name,
)


def test_positive_american_odds_convert_correctly():
    # +100 is an even-money bet.
    assert american_to_probability(100) == pytest.approx(0.5)
    # +900 implies 10%.
    assert american_to_probability(900) == pytest.approx(0.1)


def test_negative_american_odds_convert_correctly():
    # -200 implies 2/3.
    assert american_to_probability(-200) == pytest.approx(2 / 3)


def test_longer_odds_always_mean_lower_probability():
    assert american_to_probability(550) > american_to_probability(2000)


def test_parse_american_handles_the_leading_plus():
    assert parse_american("+550") == 550
    assert parse_american("-120") == -120


def test_unparseable_prices_are_dropped_not_guessed():
    # A mispriced favourite would distort the entire normalised field.
    assert parse_american("EVEN") is None
    assert parse_american(None) is None


def _raw(prices: list[tuple[str, str]]) -> dict:
    return {
        "items": [{
            "name": "Offensive Player of the Year",
            "futures": [{
                "provider": {"name": "DraftKings"},
                "books": [
                    {"athlete": {"$ref": f"https://x/athletes/{aid}?lang=en"}, "value": value}
                    for aid, value in prices
                ],
            }],
        }]
    }


def test_devigged_probabilities_sum_to_one():
    futures = _normalize(_raw([("1", "+100"), ("2", "+100"), ("3", "+100")]), "Offensive Player of the Year")

    assert sum(f.devigged_probability for f in futures) == pytest.approx(1.0, abs=1e-4)


def test_devigging_actually_removes_a_real_overround():
    # Three even-money runners imply 150% -- a 50% book margin.
    futures = _normalize(_raw([("1", "+100"), ("2", "+100"), ("3", "+100")]), "Offensive Player of the Year")

    assert market_overround(futures) == pytest.approx(1.5)
    for f in futures:
        assert f.implied_probability == pytest.approx(0.5)
        assert f.devigged_probability == pytest.approx(1 / 3, abs=1e-4)


def test_devigging_preserves_the_ordering_of_the_field():
    futures = _normalize(
        _raw([("fav", "+550"), ("mid", "+2000"), ("long", "+10000")]),
        "Offensive Player of the Year",
    )

    ordered = sorted(futures, key=lambda f: -f.devigged_probability)
    assert [f.espn_id for f in ordered] == ["fav", "mid", "long"]


def test_an_award_that_is_not_present_returns_nothing():
    assert _normalize(_raw([("1", "+100")]), "Defensive Player of the Year") == []


def test_upside_by_name_drops_unmatched_ids():
    futures = [
        FutureOdds("1", 550, 0.15, 0.6),
        FutureOdds("999", 2000, 0.05, 0.4),
    ]

    scores = upside_by_name(futures, {"1": "known player"})

    # An unmatched id is "no signal", never "no upside" -- so it is absent
    # rather than present with a zero.
    assert scores == {"known player": 0.6}
    assert "999" not in scores
