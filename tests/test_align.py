"""A-0d 정렬 검증 (예문은 직접 작성)."""

import pytest

from hnc.align import Span, align_pair, char_align, jamo_align, rebuild_x
from hnc.morph import MorphAnalyzer


@pytest.fixture(scope="module")
def ana():
    return MorphAnalyzer()


def _align(ana, y, x):
    spans = [Span(*s) for s in ana.spans(y)]
    return align_pair(y, x, spans)


def test_identical_has_no_regions(ana):
    assert _align(ana, "학교에 갔다.", "학교에 갔다.") == []


def test_particle_substitution(ana):
    y, x = "학교를 간다.", "학교을 간다."
    rs = _align(ana, y, x)
    assert len(rs) == 1
    r = rs[0]
    assert (r.y_seg, r.x_seg, r.kind) == ("를", "을", "edit")
    assert r.morphs == [("를", "JKO")]
    assert ("sub", "ᄅ", "ᄋ") in r.jamo_ops
    assert rebuild_x(y, rs) == x


def test_spacing_region(ana):
    y, x = "밥 먹었어", "밥먹었어"
    rs = _align(ana, y, x)
    assert [(r.y_seg, r.x_seg, r.kind) for r in rs] == [(" ", "", "spacing")]
    assert rs[0].jamo_ops == [("del", "<SP>", "")]
    assert rebuild_x(y, rs) == x


def test_contracted_morphemes_are_grouped(ana):
    # 갔 = 가 + 었 : 한 음절에 겹친 두 형태소가 한 구간에 함께 묶인다.
    y, x = "집에 갔다", "집에 갓다"
    rs = _align(ana, y, x)
    assert len(rs) == 1
    r = rs[0]
    assert (r.y_seg, r.x_seg) == ("갔", "갓")
    assert ("가", "VV") in r.morphs and any(t == "EP" for _, t in r.morphs)
    assert ("sub", "ᆻ", "ᆺ") in r.jamo_ops
    assert rebuild_x(y, rs) == x


def test_region_expands_to_whole_morpheme(ana):
    y, x = "하려고요.", "하려구요."
    rs = _align(ana, y, x)
    assert rebuild_x(y, rs) == x
    r = rs[0]
    assert "고" in r.y_seg and "구" in r.x_seg
    assert ("sub", "ᅩ", "ᅮ") in r.jamo_ops


@pytest.mark.parametrize("y, x", [
    ("안녕하세요.", "안녕하세요!!"),
    ("그래서 우리는 집에 갔다.", "그래서우리는 집에 갓다"),
    ("저는 학생입니다.", "저는 학생 입니다"),
    ("오늘 날씨가 좋네요.", "오늘 날시가 조네요 ㅋㅋ"),
    ("", "ㅋㅋ"),
    ("네.", ""),
    ("😀 학교에 가요.", "😀 학교애 가요"),
])
def test_rebuild_invariant(ana, y, x):
    rs = _align(ana, y, x)
    assert rebuild_x(y, rs) == x


def test_kiwi_spans_are_python_char_offsets(ana):
    text = "😀 학교에 가요."
    spans = ana.spans(text)
    hak = next(s for s in spans if s[0] == "학교")
    assert text[hak[2]:hak[3]] == "학교"


def test_char_align_prefers_space_indel_over_substitution():
    A = char_align("가 나", "가나")
    assert (1, None) in A


def test_spacing_and_spelling_edits_are_separate_regions(ana):
    y, x = "먹은 적 있어서", "먹은 적잇어서"
    rs = _align(ana, y, x)
    assert [(r.y_seg, r.x_seg, r.kind) for r in rs] == [(" ", "", "spacing"), ("있", "잇", "edit")]
    assert rebuild_x(y, rs) == x


def test_punct_region_kind(ana):
    rs = _align(ana, "좋아요.", "좋아요!!")
    assert [(r.y_seg, r.x_seg, r.kind) for r in rs] == [(".", "!!", "punct")]


def test_jamo_align_prefers_resyllabification():
    from hnc.jamo import decompose
    # 먹어 → 머거: 종성 ᆨ이 다음 음절 초성 ᄀ로 (같은 키, 위치만 다름) + ᄋ 삭제
    ops = jamo_align(decompose("먹어"), decompose("머거"))
    assert [o for o in ops if o[0] != "="] == [("sub", "ᆨ", "ᄀ"), ("del", "ᄋ", "")]
    # 으면 → 음: 초성 ᄆ이 종성 ᆷ으로 + ᅧ, ᆫ 삭제
    ops = jamo_align(decompose("으면"), decompose("음"))
    assert ("sub", "ᄆ", "ᆷ") in ops


def test_jamo_align_ops():
    assert jamo_align(["ᄀ", "ᅡ"], ["ᄀ", "ᅡ", "ᆨ"]) == [("=", "ᄀ", "ᄀ"), ("=", "ᅡ", "ᅡ"), ("ins", "", "ᆨ")]
