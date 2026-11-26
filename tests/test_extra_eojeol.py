import math
from pathlib import Path

import pytest

from hnc.extra_eojeol import CONTEXTS, ExtraEojeol, insertion_slots


@pytest.fixture
def model():
    trained, info = ExtraEojeol.fit([("또 난다", "난다"), ("난다 또", "난다"), ("새 더 난다", "새 난다"),
                                     ("새 새 난다", "새 난다"), ("새 난다", "새 난다"),
                                     ("새 ! 난다", "새 난다"), ("새", "")])
    assert info == {"pairs": 7, "empty_y": 1, "events": 4, "repeated_slots": 1, "punctuation_excluded": 1}
    return trained


def test_exact_repeated_slots_count_one_observation(model):
    assert list(insertion_slots(["새", "난다"], ["새", "새", "난다"])) == [0, 1]
    assert model.counts["start"]["새"] == model.counts["interior"]["새"] == .5
    assert model.opportunities == {"start": 6, "interior": 4, "end": 6}
    assert sum(sum(model.counts[k].values()) for k in CONTEXTS) == 4


def test_copy_mass_is_conserved_over_distinct_inserted_outputs(model):
    y = "새 난다"
    words = y.split()
    copy_cost = -math.log(.6)
    xs = {" ".join([*words[:i], w, *words[i:]]) for i in range(len(words)+1)
          for w in model.log_emission["start"]}
    base = math.exp(-copy_cost)
    retained = math.exp(-model.adjust(y, y, copy_cost, lambda _: copy_cost).pilot_cost)
    extra = 0.
    for x in xs:
        a = model.adjust(y, x, 11., lambda _: copy_cost)
        assert a.kind == "trained" and a.reserve_cost == 11. and 0. < a.event_posterior < 1.
        extra += math.exp(-a.pilot_cost) - math.exp(-11.)
    assert 0. < retained < base and extra == pytest.approx(base - retained, abs=1e-12)


@pytest.mark.parametrize("y,x,kind", [
    ("새 난다", "새 전혀 난다", "unseen"), ("새 난다", "새 ! 난다", "punctuation"),
    ("새 난다", "새 더 잔다", "other"), ("새 난다", "새 아주 더 난다", "other"),
    ("", "또", "other"), ("새 난다", "새난다", "other")])
def test_unlicensed_edits_keep_the_old_channel(model, y, x, kind):
    def unexpected_identity(_):
        pytest.fail("no new route is possible")
    adjustment = model.adjust(y, x, 12., unexpected_identity)
    assert adjustment.kind == kind and adjustment.pilot_cost == adjustment.reserve_cost == 12.


def test_rerank_and_reference_unmatched_deletion_are_distinct(model, monkeypatch):
    from scripts.c_extra_eojeol import assessed, rerank
    from scripts.c_metric import annotations
    scorer = Path("tools/m2scorer/scripts").resolve()
    if not (scorer / "levenshtein.py").is_file():
        pytest.skip("M² scorer not installed")
    monkeypatch.syspath_prepend(str(scorer))
    x, y = "새 더 난다", "새 난다"
    hs = [{"text": x, "ch": 1., "prior": -12., "s2s": -1.},
          {"text": y, "ch": 40., "prior": -1., "s2s": -1.}]
    assert rerank(x, hs, model, lambda _: 1.)[0] == (0, 0, 1)
    good = annotations(["S " + x, "A 1 2|||DELETION|||-NONE-|||REQUIRED|||-NONE-|||0"])
    bad = annotations(["S " + x, "A -1 -1|||noop|||-NONE-|||REQUIRED|||-NONE-|||0"])
    right = assessed(y, *good, reference=y)
    wrong = assessed(y, *bad, reference=x)
    assert right["del_tp"] == [0] and right["del_fp"] == []
    assert wrong["del_tp"] == [] and wrong["del_fp"] == [1] and wrong["pure_unmatched"]
    assert not assessed(y, *bad, reference=y)["del_fp"]
