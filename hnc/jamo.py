"""자모 표상 v2: 위치 구분 자모 + 겹자모 타건 분해 + <SP>.

설계서 §1.1 결정(2026-11-24)을 구현한다. `phdq_noisy/noisy_channel/jamo.py`(호환 자모,
초·종성 통합)에서 출발했으나 표상이 다르므로 새로 작성했다.

표상 규약
--------
- 초성·중성·종성을 **서로 다른 심볼**로 둔다. 유니코드 조합형 자모 블록을 쓴다:
  초성 U+1100–U+1112, 중성 U+1161–U+1175, 종성 U+11A8–U+11C2.
  예: 초성 ㄱ = ``ᄀ``(U+1100), 종성 ㄱ = ``ᆨ``(U+11A8).
- 겹받침·겹모음은 두벌식 **타건 단위**로 분해한다(ㄺ→ᆯ+ᆨ, ㅘ→ᅩ+ᅡ). 쌍자음(ㄲ, ㅆ 등)과
  ㅐ·ㅔ·ㅒ·ㅖ는 한 번의 타건(Shift 포함)이므로 단일 심볼이다.
- 공백(U+0020)은 ``<SP>`` 심볼 하나로 바꾼다.
- 한글 음절이 아닌 문자(숫자·라틴·문장부호·호환 자모 단독 ``ㅋ`` 등)는 그 문자 자체가
  심볼이다. 입력에 조합형 자모 문자가 그대로 들어 있으면 자모 심볼과 섞이지 않도록
  ``\\`` 접두 심볼(예: ``\\ᄀ``)로 감싼다.
- 입력은 NFC로 정규화한 뒤 분해한다. 따라서 왕복 무결성은
  ``compose(decompose(s)) == unicodedata.normalize("NFC", s)`` 이다.

알파벳(``ALPHABET``) = 초성 19 + 중성 14 + 종성 16 + ``<SP>`` = 50 심볼(한글 외 문자 제외).
"""

from __future__ import annotations

import unicodedata
from typing import Iterable, List

# --- 유니코드 상수 (이 프로젝트에서 한 곳에서만 정의) ---------------------------
HANGUL_BASE = 0xAC00
HANGUL_END = 0xD7A3
NUM_CHOSEONG = 19
NUM_JUNGSEONG = 21
NUM_JONGSEONG = 28  # 종성 없음(인덱스 0) 포함
_JUNG_JONG = NUM_JUNGSEONG * NUM_JONGSEONG  # 588

CHO_BASE = 0x1100
JUNG_BASE = 0x1161
JONG_BASE = 0x11A8  # 종성 인덱스 1에 대응

SP = "<SP>"
ESCAPE = "\\"

# 인덱스 → 조합형 자모
CHOSEONG = [chr(CHO_BASE + i) for i in range(NUM_CHOSEONG)]
JUNGSEONG = [chr(JUNG_BASE + i) for i in range(NUM_JUNGSEONG)]
JONGSEONG = [""] + [chr(JONG_BASE + i) for i in range(NUM_JONGSEONG - 1)]

_CHO_INDEX = {c: i for i, c in enumerate(CHOSEONG)}
_JUNG_INDEX = {c: i for i, c in enumerate(JUNGSEONG)}
_JONG_INDEX = {c: i for i, c in enumerate(JONGSEONG) if c}

# --- 겹자모 → 타건 분해 표 (위치 보존) ------------------------------------------
# 겹받침 11개: 두 종성 심볼로.
_JONG_SPLIT = {
    "\u11AA": ("\u11A8", "\u11BA"),  # ㄳ → ㄱ ㅅ
    "\u11AC": ("\u11AB", "\u11BD"),  # ㄵ → ㄴ ㅈ
    "\u11AD": ("\u11AB", "\u11C2"),  # ㄶ → ㄴ ㅎ
    "\u11B0": ("\u11AF", "\u11A8"),  # ㄺ → ㄹ ㄱ
    "\u11B1": ("\u11AF", "\u11B7"),  # ㄻ → ㄹ ㅁ
    "\u11B2": ("\u11AF", "\u11B8"),  # ㄼ → ㄹ ㅂ
    "\u11B3": ("\u11AF", "\u11BA"),  # ㄽ → ㄹ ㅅ
    "\u11B4": ("\u11AF", "\u11C0"),  # ㄾ → ㄹ ㅌ
    "\u11B5": ("\u11AF", "\u11C1"),  # ㄿ → ㄹ ㅍ
    "\u11B6": ("\u11AF", "\u11C2"),  # ㅀ → ㄹ ㅎ
    "\u11B9": ("\u11B8", "\u11BA"),  # ㅄ → ㅂ ㅅ
}
# 겹모음 7개: 두 중성 심볼로.
_JUNG_SPLIT = {
    "\u116A": ("\u1169", "\u1161"),  # ㅘ → ㅗ ㅏ
    "\u116B": ("\u1169", "\u1162"),  # ㅙ → ㅗ ㅐ
    "\u116C": ("\u1169", "\u1175"),  # ㅚ → ㅗ ㅣ
    "\u116F": ("\u116E", "\u1165"),  # ㅝ → ㅜ ㅓ
    "\u1170": ("\u116E", "\u1166"),  # ㅞ → ㅜ ㅔ
    "\u1171": ("\u116E", "\u1175"),  # ㅟ → ㅜ ㅣ
    "\u1174": ("\u1173", "\u1175"),  # ㅢ → ㅡ ㅣ
}
_JONG_JOIN = {v: k for k, v in _JONG_SPLIT.items()}
_JUNG_JOIN = {v: k for k, v in _JUNG_SPLIT.items()}

# --- 알파벳 -------------------------------------------------------------------
KEYSTROKE_CHOSEONG = list(CHOSEONG)  # 19 (쌍자음 포함, 모두 단일 타건)
KEYSTROKE_JUNGSEONG = [j for j in JUNGSEONG if j not in _JUNG_SPLIT]  # 14
KEYSTROKE_JONGSEONG = [j for j in JONGSEONG if j and j not in _JONG_SPLIT]  # 16
ALPHABET = KEYSTROKE_CHOSEONG + KEYSTROKE_JUNGSEONG + KEYSTROKE_JONGSEONG + [SP]

assert len(KEYSTROKE_JUNGSEONG) == 14
assert len(KEYSTROKE_JONGSEONG) == 16
assert len(ALPHABET) == 50

_CHO_SET = set(KEYSTROKE_CHOSEONG)
_JUNG_SET = set(KEYSTROKE_JUNGSEONG)
_JONG_SET = set(KEYSTROKE_JONGSEONG)

# --- 위치 심볼 → 두벌식 키(호환 자모) — 자판 prior용 -------------------------------
# 같은 키로 입력되는 초성·종성(예: ᄀ, ᆨ)은 같은 키 문자 'ㄱ'에 대응한다.
_CHO_COMPAT = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG_COMPAT = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG_COMPAT = "ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
KEY_OF = {}
KEY_OF.update({c: _CHO_COMPAT[i] for i, c in enumerate(CHOSEONG)})
KEY_OF.update({c: _JUNG_COMPAT[i] for i, c in enumerate(JUNGSEONG) if c in _JUNG_SET})
KEY_OF.update({c: _JONG_COMPAT[i - 1] for i, c in enumerate(JONGSEONG) if c in _JONG_SET})
KEY_OF[SP] = " "


# --- 분류 헬퍼 ----------------------------------------------------------------
def is_syllable(ch: str) -> bool:
    return len(ch) == 1 and HANGUL_BASE <= ord(ch) <= HANGUL_END


def is_choseong(sym: str) -> bool:
    return sym in _CHO_SET


def is_jungseong(sym: str) -> bool:
    return sym in _JUNG_SET


def is_jongseong(sym: str) -> bool:
    return sym in _JONG_SET


def is_jamo(sym: str) -> bool:
    return sym in _CHO_SET or sym in _JUNG_SET or sym in _JONG_SET


def position(sym: str) -> str:
    """심볼의 위치 범주: 'cho' | 'jung' | 'jong' | 'sp' | 'other'."""
    if sym in _CHO_SET:
        return "cho"
    if sym in _JUNG_SET:
        return "jung"
    if sym in _JONG_SET:
        return "jong"
    if sym == SP:
        return "sp"
    return "other"


def _is_conjoining(ch: str) -> bool:
    """조합형 자모 블록(U+1100–U+11FF, 확장 A·B) 문자인가."""
    o = ord(ch)
    return 0x1100 <= o <= 0x11FF or 0xA960 <= o <= 0xA97F or 0xD7B0 <= o <= 0xD7FF


# --- 분해 ---------------------------------------------------------------------
def split_syllable(ch: str) -> tuple:
    """완성형 음절 1자 → (초성, 중성, 종성) 조합형 자모. 종성 없으면 ''."""
    if not is_syllable(ch):
        raise ValueError(f"완성형 한글 음절이 아님: {ch!r}")
    off = ord(ch) - HANGUL_BASE
    return (CHOSEONG[off // _JUNG_JONG],
            JUNGSEONG[(off % _JUNG_JONG) // NUM_JONGSEONG],
            JONGSEONG[off % NUM_JONGSEONG])


def decompose(text: str) -> List[str]:
    """문자열 → 심볼 열 (위치 구분 자모, 겹자모 타건 분해, 공백 <SP>)."""
    out: List[str] = []
    for ch in unicodedata.normalize("NFC", text):
        if is_syllable(ch):
            cho, jung, jong = split_syllable(ch)
            out.append(cho)
            out.extend(_JUNG_SPLIT.get(jung, (jung,)))
            if jong:
                out.extend(_JONG_SPLIT.get(jong, (jong,)))
        elif ch == " ":
            out.append(SP)
        elif _is_conjoining(ch):
            out.append(ESCAPE + ch)
        else:
            out.append(ch)
    return out


# --- 조합 ---------------------------------------------------------------------
def build_syllable(cho: str, jung: str, jong: str = "") -> str:
    """조합형 (초성, 중성[, 종성]) → 완성형 음절. 겹자모는 이미 합쳐진 상태여야 한다."""
    code = (HANGUL_BASE
            + _CHO_INDEX[cho] * _JUNG_JONG
            + _JUNG_INDEX[jung] * NUM_JONGSEONG
            + (_JONG_INDEX[jong] if jong else 0))
    return chr(code)


def _compose(symbols: List[str], strict: bool) -> str:
    out: List[str] = []
    i, n = 0, len(symbols)
    while i < n:
        s = symbols[i]
        if s in _CHO_SET:
            # 초성 + 중성(1~2) + 종성(0~2)
            if i + 1 >= n or symbols[i + 1] not in _JUNG_SET:
                if strict:
                    raise ValueError(f"초성 뒤에 중성이 없음 (위치 {i})")
                out.append(KEY_OF[s])
                i += 1
                continue
            jung = symbols[i + 1]
            j = i + 2
            if j < n and (jung, symbols[j]) in _JUNG_JOIN:
                jung = _JUNG_JOIN[(jung, symbols[j])]
                j += 1
            jong = ""
            if j < n and symbols[j] in _JONG_SET:
                jong = symbols[j]
                j += 1
                if j < n and (jong, symbols[j]) in _JONG_JOIN:
                    jong = _JONG_JOIN[(jong, symbols[j])]
                    j += 1
            out.append(build_syllable(cho=s, jung=jung, jong=jong))
            i = j
        elif s in _JUNG_SET or s in _JONG_SET:
            if strict:
                raise ValueError(f"음절에 속하지 않은 {position(s)} 심볼 {s!r} (위치 {i})")
            out.append(KEY_OF[s])
            i += 1
        elif s == SP:
            out.append(" ")
            i += 1
        elif len(s) == 2 and s[0] == ESCAPE:
            out.append(s[1])
            i += 1
        else:
            out.append(s)
            i += 1
    return "".join(out)


def compose(symbols: Iterable[str]) -> str:
    """심볼 열 → 문자열. 음절로 조합할 수 없는 자모 배열이면 ValueError."""
    return _compose(list(symbols), strict=True)


def compose_lenient(symbols: Iterable[str]) -> str:
    """표시용 조합: 음절에 속하지 못한 자모는 호환 자모 문자로 출력한다(역변환 불가)."""
    return _compose(list(symbols), strict=False)


def is_valid(symbols: Iterable[str]) -> bool:
    """`compose`가 성공하고 그 결과를 다시 분해하면 같은 심볼 열이 되는가.

    두 번째 조건은 비정규 배열(예: 겹받침이 될 수 없는 종성 둘, 겹모음이 아닌 중성 둘)을
    걸러낸다. 이런 배열은 조합 과정에서 남는 심볼이 생겨 실패하거나 다른 열로 바뀐다.
    """
    seq = list(symbols)
    try:
        text = _compose(seq, strict=True)
    except ValueError:
        return False
    return decompose(text) == seq
