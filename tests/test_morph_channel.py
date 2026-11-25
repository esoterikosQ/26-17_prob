"""A-3a 형태소 편집 추출: 형태소 정렬, 자동 z, 통용 변이 판정 (예시는 직접 만든 것)."""

from hnc.morph_channel import (
    KAGAS_TO_Z, align_morphs, classify_edit, is_punct, morph_edits, region_family, region_z, strip_punct,
    surface_spell, variant_by_freq,
)

FREQ = {"정말": 92549, "완전": 19851, "있어서": 43734, "잇어서": 2, "라테": 499, "라떼": 1158, "나는": 5000, "저는": 9000}
SYS = frozenset({(("정말", "MAG"), ("완전", "MAG"))})


def freq(w):
    return FREQ.get(w, 0)


def zs(my, mx, variant=lambda a, b: False):
    return [e["z"] for e in morph_edits(my, mx, variant)]


def test_particle_ending_insertion_deletion():
    assert zs([("학교", "NNG"), ("를", "JKO")], [("학교", "NNG"), ("을", "JKO")]) == ["IDENT", "PARTICLE"]
    assert zs([("가", "VV"), ("았", "EP"), ("다", "EF")], [("가", "VV"), ("았", "EP"), ("고", "EC")])[-1] == "ENDING"
    # 쓴 사람이 조사를 더 씀(x에만 있음) = INS, 빠뜨림 = DEL
    assert zs([("정년", "NNG")], [("정년", "NNG"), ("을", "JKO")]) == ["IDENT", "INS"]
    assert zs([("나", "NP"), ("는", "JX")], [("나", "NP")]) == ["IDENT", "DEL"]


def test_alignment_prefers_same_class_substitution():
    pairs = align_morphs([("책", "NNG"), ("을", "JKO"), ("읽", "VV")], [("책", "NNG"), ("이", "JKS"), ("읽", "VV")])
    assert pairs[1] == (("을", "JKO"), ("이", "JKS"))


def test_spelling_vs_variant_vs_lexical():
    typo = variant_by_freq(freq, "있어서", "잇어서")
    assert classify_edit(("있", "VA"), ("잇", "NNG"), typo) == "SPELL"
    colloquial = variant_by_freq(freq, "정말", "완전", SYS)
    assert classify_edit(("정말", "MAG"), ("완전", "MAG"), colloquial) == "VARIANT"
    # 흔한 실재어라도 체계적 규범화 쌍이 아니고 표기 변이도 아니면 어휘 대치(학습자 오용)
    register = variant_by_freq(freq, "나는", "저는", SYS)
    assert classify_edit(("나", "NP"), ("저", "NP"), register) == "LEXICAL"
    loan = variant_by_freq(freq, "라테", "라떼")
    assert classify_edit(("라테", "NNP"), ("라떼", "NNP"), loan) == "VARIANT"   # 자모 1개 차이라도 통용 표기
    assert classify_edit(("사과", "NNG"), ("배추", "NNG")) == "LEXICAL"


def test_variant_falls_back_to_free_morpheme_forms():
    # 어절 빈도가 둘 다 0이면 홀로 쓰이는 형태끼리 비교
    v = variant_by_freq(lambda w: {"정말": 100, "완전": 50}.get(w, 0), "정말이지요", "완전이지요", SYS)
    assert v(("정말", "MAG"), ("완전", "MAG"))
    assert not v(("있", "VA"), ("잇", "VV"))      # 용언 어간은 형태 빈도로 판정하지 않음


def test_punct_stripping_keeps_jamo_sw():
    assert is_punct((",", "SP")) and is_punct(("!", "SF"))
    assert not is_punct(("ㅇㅇ", "SW"))
    assert strip_punct([("그래", "IC"), (",", "SP")]) == [("그래", "IC")]
    assert zs([("그래", "IC"), (",", "SP")], [("그래", "IC")]) == ["IDENT"]


def test_region_z_priority_and_surface_only():
    assert region_z([{"z": "IDENT"}, {"z": "IDENT"}]) == "SPELL"
    assert region_z([{"z": "SPELL"}, {"z": "PARTICLE"}]) == "PARTICLE"
    assert KAGAS_TO_Z["INSERTION"] == "DEL" and KAGAS_TO_Z["DELETION"] == "INS"


def test_surface_spell_overrides_typo_misanalysis():
    # 날씨 → 날시: Kiwi가 x를 날/NNG + 시/NNG로 쪼갬 → 내용 형태소가 달라도 표층이 자모 1개 차이 → SPELL
    e = morph_edits([("날씨", "NNG")], [("날", "NNG"), ("시", "NNG")])
    fixed = surface_spell(e, "날씨", "날시")
    assert [x["z"] for x in fixed] == ["IDENT"] and region_z(fixed) == "SPELL"
    # 조사만 다른 경우(를 → 을)는 표층이 가까워도 그대로 조사 대치
    e2 = morph_edits([("학교", "NNG"), ("를", "JKO")], [("학교", "NNG"), ("을", "JKO")])
    assert region_z(surface_spell(e2, "학교를", "학교을")) == "PARTICLE"


def test_region_family_groups_particle_omission():
    e = morph_edits([("학교", "NNG"), ("에", "JKB")], [("학교", "NNG")])
    assert region_z(e) == "DEL" and region_family(e) == "PARTICLE"
    e2 = morph_edits([("가", "VV"), ("았", "EP")], [("가", "VV")])
    assert region_family(e2) == "ENDING"


def _toy_channel():
    from hnc.morph_channel import MorphChannel
    regions = ([{"edits": [{"m": ["를", "JKO"], "m2": ["을", "JKO"], "z": "PARTICLE", "lt": "NNG"}]}] * 3
               + [{"edits": [{"m": ["에", "JKB"], "m2": None, "z": "DEL", "lt": "NNG"}]}] * 4
               + [{"edits": [{"m": None, "m2": ["을", "JKO"], "z": "INS", "lt": "NNG"}]}] * 2)
    uni = {("를", "JKO"): 30, ("을", "JKO"): 50, ("에", "JKB"): 40, ("학교", "NNG"): 100}
    bi = {("NNG", "에", "JKB"): 20, ("NNG", "를", "JKO"): 30, ("NNG", "을", "JKO"): 50}
    return MorphChannel.fit(regions, uni, bi, beta=5.0, min_count=2)


def test_morph_channel_fit_and_candidates():
    import math
    from hnc.morph_channel import MorphChannel
    ch = _toy_channel()
    assert ch.sub["을/JKO"][0][:2] == [["를", "JKO"], "PARTICLE"]
    assert ch.sub["을/JKO"][0][2] == math.log(35 / 3)                    # −log(3 / (30 + 5))
    assert ch.sub["을/JKO"][0][3] == 3                                    # 사건 빈도(후보 선택 순서)
    cands = {(tuple(map(tuple, ms)), z): d for ms, z, d in ch.candidates([("학교", "NNG"), ("을", "JKO")])}
    assert ((("학교", "NNG"), ("를", "JKO")), "PARTICLE") in cands          # 조사 대치
    assert ((("학교", "NNG"),), "PARTICLE_INS") in cands                     # 더 쓴 조사 제거
    assert ((("학교", "NNG"), ("에", "JKB"), ("을", "JKO")), "PARTICLE_DEL") in cands  # 빠뜨린 조사 복원
    assert all(math.isfinite(d) for d in cands.values())
    assert MorphChannel.from_json(ch.to_json()).sub == ch.sub


def test_candidates_ranked_by_event_count_not_rate():
    from hnc.morph_channel import MorphChannel
    # 드문 형태소(빈도 2, 비율 높음)보다 흔한 조사(빈도 40, 비율 낮음)의 누락 복원을 먼저 고른다
    regions = ([{"edits": [{"m": ["는", "JX"], "m2": None, "z": "DEL", "lt": "NP"}]}] * 40
               + [{"edits": [{"m": ["ᆫ", "JX"], "m2": None, "z": "DEL", "lt": "NP"}]}] * 2)
    uni = {("는", "JX"): 4000, ("ᆫ", "JX"): 3, ("우리", "NP"): 500}
    bi = {("NP", "는", "JX"): 2000, ("NP", "ᆫ", "JX"): 3}
    ch = MorphChannel.fit(regions, uni, bi)
    top = ch.candidates([("우리", "NP")], max_cands=1)
    assert top[0][0] == [("우리", "NP"), ("는", "JX")] and top[0][1] == "PARTICLE_DEL"


def test_lexical_candidates_only_attested_same_pos_substitutions():
    from hnc.morph_channel import MorphChannel
    sub = {"빵/NNG": [[["떡", "NNG"], "LEXICAL", 3., 5],
                      [["콩", "NNG"], "LEXICAL", .01, 4],
                      [["서울", "NNP"], "LEXICAL", .01, 20],
                      [["밥", "NNG"], "VARIANT", .01, 30]],
           "열/VV-R": [[["닫", "VV"], "LEXICAL", 4., 8]],
           "가람/NNP": [[["떡", "NNG"], "LEXICAL", .01, 40]]}
    mc = MorphChannel(sub, {"열/VV-R": .25}, {}, {})
    ms = [("가람", "NNP"), ("빵", "NNG"), ("열", "VV-R"), ("다", "EF")]
    out = mc.lexical_candidates(ms, 2)
    assert out == [([*ms[:2], ("닫", "VV"), ms[-1]], "LEXICAL_VERB", 3.75),
                   ([ms[0], ("떡", "NNG"), *ms[2:]], "LEXICAL_NOUN", 3.)]
    assert mc.lexical_candidates(ms, 1) == out[:1] and mc.lexical_candidates(ms, 0) == []
