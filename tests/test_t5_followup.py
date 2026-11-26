import pytest

from scripts import t5_followup as run
from scripts import t5_followup_score as score


def test_t5_four_way_comparison_preserves_pool_and_score_controls():
    def hyp(text,s,h,prior,ch,lp):
        return dict(text=text,s=s,h=h,prior=prior,ch=ch,s2s=lp)
    hs=[hyp("a",0,None,-4,1,-3),hyp("b",1,None,-3,1,-1),hyp("c",None,0,-1,0,-8),
        hyp("d",None,1,-2,0,-2)]
    assert run.choices(hs)=={"r9":0,"s_only":1,"r10":2,"r11":3}
    with pytest.raises(ValueError,match="ambiguous"):
        run.choices([*hs,dict(hs[1],s=0,text="e")])


def test_followup_does_not_open_references_without_its_own_seal(monkeypatch):
    def blocked():
        raise FileNotFoundError("T5 seal absent")
    monkeypatch.setattr(score,"seal",blocked)
    monkeypatch.setattr(score,"reference",lambda d:pytest.fail("gold not gated"))
    with pytest.raises(FileNotFoundError,match="T5 seal absent"):
        score.open_test("union")
