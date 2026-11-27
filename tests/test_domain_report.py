import pytest

from hnc.candidates_c import Counts, aggregate
from scripts.c_strong_dev import ORDER
from scripts.domain_report import mix_terms, overlap, standardized_recall


def test_other_reference_is_not_necessarily_a_conflicting_only_training_pair():
    assert [overlap(s,"a") for s in (set(),{"b"},{"a"},{"a","b"})] == [
        "unseen_x","only_other_y","only_same_y","same_and_other_y"]


def test_micro_change_uses_both_tp_and_proposal_count_not_sentence_weighted_f_changes():
    a=[Counts(20,23,50),Counts(4,5,8),Counts(1,4,5)]
    b=[Counts(12,20,50),Counts(7,7,8),Counts(1,3,5)]
    def part(left,right):
        return {"scores":{"r9":left.metrics(),"r11":right.metrics()}}
    x={k:part(l,r) for k,l,r in zip(ORDER,a,b,strict=True)}
    total=part(aggregate(a),aggregate(b))
    c=mix_terms({**x,"all_test":total})
    assert c["lang8"]["contribution_points"]<0<c["korean_learner"]["contribution_points"]
    assert sum(i["contribution_points"] for i in c.values())==pytest.approx((aggregate(b).f-aggregate(a).f)*100)
    with pytest.raises(ValueError,match="do not add up"):
        mix_terms({**x,"all_test":part(a[0],b[0])})


def test_type_standardization_changes_mix_but_not_within_category_recall():
    def part(s,i):
        return {"by_type":{"SPELL":{"gold":s,"r9_hit":s},"INSERTION":{"gold":i,"r9_hit":0}},
                "scores":{"r9":{"R":s/(s+i)}}}
    result=standardized_recall(part(8,2),part(2,8),"r9")
    assert result=={"learner":.8,"learner_under_lang8_type_mix":.2,"lang8":.2}
