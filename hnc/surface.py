"""어절 표면 처리: 앞뒤 부호와 핵심(한글·영숫자로 시작하고 끝나는 부분)을 나눈다."""

from __future__ import annotations

from typing import Tuple


def _is_word_char(ch: str) -> bool:
    return ch.isalnum()


def split_punct(token: str) -> Tuple[str, str, str]:
    """``"(사과를)."`` → ``("(", "사과를", ").")``. 핵심이 없으면 ``(token, "", "")``."""
    i, j = 0, len(token)
    while i < j and not _is_word_char(token[i]):
        i += 1
    while j > i and not _is_word_char(token[j - 1]):
        j -= 1
    if i == j:
        return token, "", ""
    return token[:i], token[i:j], token[j:]


def has_hangul(s: str) -> bool:
    return any("가" <= ch <= "힣" for ch in s)
