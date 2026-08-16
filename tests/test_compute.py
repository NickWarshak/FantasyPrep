from fantasyprep.adp_gap.compute import compute_gaps, tolerance_for_adp
from fantasyprep.players.normalize import MatchedPlayer
from fantasyprep.sources.espn import EspnPlayer
from fantasyprep.sources.manual_adp import SharpAdpEntry


def _matched(espn_adp, sharp_adp, position="WR") -> MatchedPlayer:
    espn = EspnPlayer(espn_id=1, name="Test Player", position=position, team="XXX", espn_adp=espn_adp, espn_expert_rank=None)
    sharp = SharpAdpEntry(player_name="Test Player", team="XXX", position=position, adp=sharp_adp, source="underdog")
    return MatchedPlayer(espn=espn, sharp=sharp, match_confidence=100)


def test_tolerance_grows_with_adp():
    assert tolerance_for_adp(10) < tolerance_for_adp(50)
    assert tolerance_for_adp(50) < tolerance_for_adp(100)
    assert tolerance_for_adp(100) < tolerance_for_adp(200)


def test_positive_raw_gap_when_sharp_drafts_earlier():
    gaps = compute_gaps([_matched(espn_adp=80.0, sharp_adp=40.0)])
    assert gaps[0].raw_gap == 40.0
    assert gaps[0].adjusted_score > 0


def test_negative_raw_gap_when_espn_drafts_earlier():
    gaps = compute_gaps([_matched(espn_adp=20.0, sharp_adp=60.0)])
    assert gaps[0].raw_gap == -40.0
    assert gaps[0].adjusted_score < 0


def test_same_raw_gap_scores_higher_adjusted_early_than_late():
    early = compute_gaps([_matched(espn_adp=20.0, sharp_adp=10.0)])[0]
    late = compute_gaps([_matched(espn_adp=140.0, sharp_adp=130.0)])[0]
    assert early.raw_gap == late.raw_gap == 10.0
    assert early.adjusted_score > late.adjusted_score


def test_sorted_descending_by_adjusted_score():
    gaps = compute_gaps(
        [
            _matched(espn_adp=50.0, sharp_adp=48.0),
            _matched(espn_adp=50.0, sharp_adp=10.0),
            _matched(espn_adp=50.0, sharp_adp=90.0),
        ]
    )
    scores = [g.adjusted_score for g in gaps]
    assert scores == sorted(scores, reverse=True)
