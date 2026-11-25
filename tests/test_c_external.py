import itertools
import math
from pathlib import Path

import pytest

from hnc.candidates_c import Counts, aggregate, oracle, union_pool
from hnc.external_channel import ExternalChannel, JamoPaths, UNK, marginal
from hnc.jamo_channel import ChannelParams
from hnc.morph_channel import MorphChannel


def test_union_keeps_both_provenances_and_does_not_drop_empty_outputs():
    rows = union_pool(["가 나", "다", ""], ["가  나", "가", "다", "가"])
    assert [(r["text"], r["s"], r["h"]) for r in rows] == [
        ("가 나", 0, 0), ("가", 1, None), ("다", 2, 1), ("", None, 2)]


def test_micro_oracle_matches_cartesian_search_not_mean_sentence_f():
    pools = [[Counts(2, 9, 4), Counts(1, 1, 4)], [Counts(1, 6, 2), Counts(0, 0, 2)],
             [Counts(1, 1, 1), Counts(0, 0, 1)]]
    assert oracle(pools).f == max(aggregate(p).f for p in itertools.product(*pools))
    with pytest.raises(ValueError):
        oracle([[Counts(0, 0, 1), Counts(0, 0, 2)]])


def test_official_m2_counts(monkeypatch):
    from scripts.c_metric import annotations, per_candidate
    tool = Path("tools/m2scorer/scripts").resolve()
    if not (tool / "levenshtein.py").is_file():
        pytest.skip("M² 채점기 미설치")
    monkeypatch.syspath_prepend(str(tool))
    x, _, gold = annotations(["S 작은 새는 난다", "A 1 2|||PARTICLE|||새가|||REQUIRED|||-NONE-|||0"])
    assert per_candidate("작은 새가 난다", x, gold) == {"text": "작은 새가 난다", "tp": [0], "proposed": 1}
    assert per_candidate(x, x, gold)["tp"] == []


def test_observed_path_is_distinct_from_positive_smoothed_path():
    known = (("ᅡ",), ("ᅥ",))
    unseen = (("ᅥ",), ("ᅡ",))
    p = ChannelParams({known: .2, unseen: .1}, {"ᄀ": .95, "ᅡ": .9, "ᅥ": .9}, math.log(1e-8))
    j = JamoPaths(p, {known})
    assert j.costs("가", "거")[0] == pytest.approx(-math.log(.95 * .2))
    assert not math.isfinite(j.costs("거", "가")[0])
    assert j.costs("거", "가")[1] == pytest.approx(-math.log(.95 * .1))
    assert not math.isfinite(j.costs("€", "☃")[0])
    assert j.costs("€", "☃")[1] == pytest.approx(-2 * p.other_logp)
    assert math.isfinite(j.costs("", "☃")[1])


def test_morphology_and_backoff_all_spans():
    ch = ExternalChannel(JamoPaths(ChannelParams(), set()), MorphChannel(
        {"를/JKO": [[["가", "JKS"], "PARTICLE", 1., 8]]}, {}, {}, {}))
    cost, spans = ch.score("새가", "새를", [("새","NNG",0,1), ("가","JKS",1,2)],
                           [("새","NNG",0,1), ("를","JKO",1,2)])
    assert math.isfinite(cost) and len(spans) == 1
    assert not spans[0]["backoff_only"] and spans[0]["z"] == "PARTICLE"
    for y, x in [("€", "☃"), ("", "!"), ("!", "")]:
        c, es = ch.score(y, x, [], [])
        assert math.isfinite(c) and es[0]["backoff_only"] and es[0]["z"] == UNK
    assert ch.score("새", "새", [("새","NNG",0,1)], [("새","NNG",0,1)])[1] == []
    c, post = marginal([("JAMO", 1001), ("PARTICLE", 1002), (UNK, 1003), (UNK, 1004)])
    assert math.isfinite(c) and sum(post.values()) == pytest.approx(1)


def test_rescoring_changes_only_the_declared_score_or_pool():
    from scripts.c_eval import pick
    hyps = [{"text": "나", "s": 0, "h": None, "s2s": -1., "ch": 3., "prior": -2.},
            {"text": "가", "s": None, "h": 0, "s2s": -5., "ch": 1., "prior": -2.}]
    assert pick(hyps, "union", 0) == 1
    assert pick(hyps, "union", 1.) == 0
    assert pick(hyps, "s", 0) == 0 and pick(hyps, "h", 1.) == 1
    assert pick(hyps, "union", 0, s2s_only=True) == 0


def test_exactly_localized_m2_type_and_no_fabricated_label():
    from scripts.c_eval import single_edit_families
    from scripts.c_metric import annotations
    ann = annotations(["S 작은 새는 난다", "A 1 2|||PARTICLE|||새가|||REQUIRED|||-NONE-|||0"])
    e = {"x": [4, 5], "y": [4, 5], "z": "PARTICLE"}
    assert single_edit_families("작은 새는 난다", "작은 새가 난다", [e], ann, [0]) == [("PARTICLE", "PARTICLE")]
    assert single_edit_families("작은 새는 난다", "작은 새가 난다", [e], ann, []) == []
    assert single_edit_families("작은 새는 난다", "작은 새가 난다", [e, e], ann, [0]) == []
