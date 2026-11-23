"""A-0a 자모 표상 v2 검증: 왕복 무결성, 위치 구분, 겹자모 분해, <SP>, 유효성."""

import unicodedata

import pytest

from hnc import jamo
from hnc.jamo import SP, compose, decompose, is_valid


def test_alphabet_size_and_disjoint_positions():
    assert len(jamo.ALPHABET) == 50
    assert len(set(jamo.ALPHABET)) == 50
    cho, jung, jong = (set(jamo.KEYSTROKE_CHOSEONG), set(jamo.KEYSTROKE_JUNGSEONG),
                       set(jamo.KEYSTROKE_JONGSEONG))
    assert (len(cho), len(jung), len(jong)) == (19, 14, 16)
    assert not (cho & jung or cho & jong or jung & jong)


def test_all_syllables_round_trip_and_use_alphabet():
    alphabet = set(jamo.ALPHABET)
    for code in range(jamo.HANGUL_BASE, jamo.HANGUL_END + 1):
        ch = chr(code)
        syms = decompose(ch)
        assert compose(syms) == ch
        assert set(syms) <= alphabet
        assert is_valid(syms)
    assert jamo.HANGUL_END - jamo.HANGUL_BASE + 1 == 11172


def test_onset_and_coda_are_distinct_symbols():
    # 먹어 / 머거: 위치 구분 표상에서는 종성 ㄱ과 초성 ㄱ이 다른 심볼이다.
    assert decompose("먹어") == ["ᄆ", "ᅥ", "ᆨ", "ᄋ", "ᅥ"]
    assert decompose("머거") == ["ᄆ", "ᅥ", "ᄀ", "ᅥ"]
    assert jamo.position("ᄀ") == "cho" and jamo.position("ᆨ") == "jong"


def test_compound_split_to_keystrokes():
    assert decompose("닭") == ["ᄃ", "ᅡ", "ᆯ", "ᆨ"]       # ㄺ → ㄹ ㄱ
    assert decompose("값") == ["ᄀ", "ᅡ", "ᆸ", "ᆺ"]       # ㅄ → ㅂ ㅅ
    assert decompose("과") == ["ᄀ", "ᅩ", "ᅡ"]            # ㅘ → ㅗ ㅏ
    assert decompose("의") == ["ᄋ", "ᅳ", "ᅵ"]            # ㅢ → ㅡ ㅣ
    assert decompose("꽤") == ["ᄁ", "ᅩ", "ᅢ"]            # 쌍자음은 단일 심볼
    assert decompose("있") == ["ᄋ", "ᅵ", "ᆻ"]


def test_space_and_non_hangul_pass_through():
    text = "닭볶음탕 ㅋㅋ 2024년 ABC!  끝."
    syms = decompose(text)
    assert syms.count(SP) == text.count(" ")
    assert "ㅋ" in syms and "2" in syms and "A" in syms and "!" in syms
    assert compose(syms) == text


@pytest.mark.parametrize("text", [
    "학교에 갔었다.",
    "왜 괜찮아요? 읽었습니다, 넓죠.",
    "  앞뒤 공백  ",
    "탭\t과 줄바꿈\n도 그대로",
    "百 년 ‘따옴표’ … 😀",
    "",
])
def test_mixed_round_trip(text):
    assert compose(decompose(text)) == text


def test_nfd_input_round_trips_to_nfc():
    nfd = unicodedata.normalize("NFD", "한국어 문법")
    assert compose(decompose(nfd)) == "한국어 문법"


def test_raw_conjoining_jamo_is_escaped():
    text = "\u1100 \u11A8"  # 음절을 이루지 못한 조합형 자모 문자
    syms = decompose(text)
    assert syms == ["\\\u1100", SP, "\\\u11A8"]
    assert compose(syms) == text


@pytest.mark.parametrize("bad", [
    ["ᄀ"],                      # 초성만
    ["ᅡ"],                      # 중성만
    ["ᆨ"],                      # 종성만
    ["ᄀ", "ᅡ", "ᆨ", "ᆨ"],      # 겹받침이 아닌 종성 둘
    ["ᄀ", "ᅡ", "ᅡ"],           # 겹모음이 아닌 중성 둘
    ["ᄀ", "ᅡ", "ᆨ", "ᆺ", "ᆺ"], # 종성 셋
])
def test_invalid_sequences(bad):
    assert not is_valid(bad)
    with pytest.raises(ValueError):
        compose(bad)


def test_lenient_compose_shows_orphans_as_compat_jamo():
    assert jamo.compose_lenient(["ᄒ", "ᅡ", "ᆫ", "ᄀ"]) == "한ㄱ"


def test_key_mapping_shares_keys_across_positions():
    assert jamo.KEY_OF["ᄀ"] == jamo.KEY_OF["ᆨ"] == "ㄱ"
    assert jamo.KEY_OF["ᆻ"] == "ㅆ"
    assert jamo.KEY_OF["ᅢ"] == "ㅐ"
    assert jamo.KEY_OF[SP] == " "
    assert set(jamo.KEY_OF) == set(jamo.ALPHABET)
