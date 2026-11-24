"""Kor-* M² 파서 검증 (예문은 직접 작성; 파일 없이 줄 목록으로)."""

from hnc.kor_gec import parse_m2, same_sentence

M2 = """S 나 학교 갔어요
A 0 1|||PARTICLE|||나는|||REQUIRED|||-NONE-|||0
A 1 2|||PARTICLE|||학교에|||REQUIRED|||-NONE-|||0

S 좋아요.
A -1 -1|||noop|||-NONE-|||REQUIRED|||-NONE-|||0

S 마지막 문장
""".splitlines()


def test_parse_m2():
    sents = list(parse_m2(M2))
    assert [s.source for s in sents] == ["나 학교 갔어요", "좋아요.", "마지막 문장"]
    assert sents[0].edits == [(0, 1, "PARTICLE", "나는"), (1, 2, "PARTICLE", "학교에")]
    assert sents[1].edits == [] and sents[2].edits == []


def test_same_sentence_ignores_whitespace_runs():
    assert same_sentence("나  학교 갔어요 ", "나 학교 갔어요")
    assert not same_sentence("나 학교", "나학교")
