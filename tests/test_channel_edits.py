"""A-1a 편집쌍 추출 검증 (예문은 직접 작성; 형태소 위치 없이 정렬)."""

from collections import Counter

from hnc.align import align_pair
from hnc.channel_edits import atoms, sentence_ops, substring_counts, substring_edits
from hnc.jamo import SP, decompose


def _ops(y, x):
    return sentence_ops(y, align_pair(y, x, []))


def test_sentence_ops_cover_both_sides():
    y, x = "밥 먹었어", "밥먹엇어"
    ops = _ops(y, x)
    assert [s for _, s, _ in ops if s] == decompose(y)
    assert [s for _, _, s in ops if s] == decompose(x)


def test_coda_substitution_with_context():
    ops = _ops("갔다", "갓다")
    assert atoms(ops) == [(2, 3)]
    got = {(a, b) for a, b, _ in substring_edits(ops)}
    assert got == {(("ᆻ",), ("ᆺ",)), (("ᅡ", "ᆻ"), ("ᅡ", "ᆺ")),
                   (("ᆻ", "ᄃ"), ("ᆺ", "ᄃ")), (("ᅡ", "ᆻ", "ᄃ"), ("ᅡ", "ᆺ", "ᄃ"))}
    cores = [(a, b) for a, b, core in substring_edits(ops) if core]
    assert cores == [(("ᆻ",), ("ᆺ",))]


def test_space_deletion_edits():
    edits = {(a, b) for a, b, _ in substring_edits(_ops("밥 먹어", "밥먹어"))}
    assert ((SP,), ()) in edits
    assert (("ᆸ", SP), ("ᆸ",)) in edits and (("ᆸ", SP, "ᄆ"), ("ᆸ", "ᄆ")) in edits


def test_out_of_scope_atoms_are_counted_not_emitted():
    st = Counter()
    assert list(substring_edits(_ops("좋아요.", "좋아요!"), stats=st)) == []
    assert st["atom_other"] == 1
    st = Counter()
    list(substring_edits(_ops("학교에서", "학교로"), stats=st))   # 에서 → 로: 자모 4개 이상
    assert st["atom_long"] == 1


def test_substring_counts():
    c = substring_counts(["a", "b"], max_len=3)
    assert c == Counter({(): 3, ("a",): 1, ("b",): 1, ("a", "b"): 1})


def test_context_must_be_in_scope():
    edits = list(substring_edits(_ops("갔!", "갓!")))
    assert edits and all("!" not in a + b for a, b, _ in edits)
