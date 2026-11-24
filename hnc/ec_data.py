"""A-0c EC 쌍 분류·분할 규칙.

- x = ``form``(원문에서 말뭉치 표식을 지운 정제본), y = ``corrected_form``.
- 쌍 분류(``classify``): 편집이 어느 층위에 있는지를 가른다. 채널 학습·평가 범위를 고를 때 쓴다.
- 분할(``split_of``): 문서 id의 MD5 해시로 고정 분할한다(train 90 / dev 5 / test 5).
  같은 문서의 문장이 여러 분할에 흩어지지 않는다.
"""

from __future__ import annotations

import hashlib
import re

from .corpus import has_placeholder

# 내용 문자 = 한글 음절·호환 자모·숫자·라틴 등 문자/숫자. 그 외(문장부호·기호·이모지)는 부호로 본다.
_WS = re.compile(r"\s+")


def _content_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum())


def _no_space(s: str) -> str:
    return _WS.sub("", s)


def _no_punct(s: str) -> str:
    """부호를 지우고 공백 배치는 정규화해 남긴다."""
    return " ".join("".join(ch for ch in s if ch.isalnum() or ch.isspace()).split())


CATEGORIES = ("empty", "placeholder", "identical", "space_only", "punct_only",
              "space_punct", "content")


def classify(x: str, y: str, check_placeholder: bool = True) -> str:
    """(x, y) 쌍의 편집 층위. ``check_placeholder``는 EC 말뭉치 표식 검사(다른 자료에서는 끈다).

    - empty: 어느 한쪽이 공백뿐
    - placeholder: 말뭉치 표식이 남아 있음
    - identical: 완전히 같음
    - space_only: 공백만 다름 (공백을 지우면 같음)
    - punct_only: 부호만 다름 (공백 배치는 같고, 부호를 지우면 같음)
    - space_punct: 공백과 부호만 다름
    - content: 문자·숫자 내용이 다름 (철자·형태·어휘 교정)
    """
    if not x.strip() or not y.strip():
        return "empty"
    if check_placeholder and (has_placeholder(x) or has_placeholder(y)):
        return "placeholder"
    if x == y:
        return "identical"
    if _no_space(x) == _no_space(y):
        return "space_only"
    if _content_only(x) != _content_only(y):
        return "content"
    # 내용은 같고 공백을 지워도 다름 → 부호가 다름. 공백 배치까지 같은지로 둘을 가른다.
    if _no_punct(x) == _no_punct(y):
        return "punct_only"
    return "space_punct"


def split_of(doc_id: str) -> str:
    h = int(hashlib.md5(doc_id.encode("utf-8")).hexdigest(), 16) % 100
    if h < 90:
        return "train"
    if h < 95:
        return "dev"
    return "test"
