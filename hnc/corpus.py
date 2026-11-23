"""모두의 말뭉치 JSON 스트리밍 리더.

`phdq_noisy/noisy_channel/corpus.py`의 EC·신문 리더에서 가져와, 문서 id를 함께 내도록
고치고 형태 분석 말뭉치(MP) 리더를 추가했다. 대용량 파일을 ijson으로 스트리밍한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import ijson

# 말뭉치 표식: <태그>, {emoji:...}, &name& 류 익명화 토큰
_TAG = re.compile(r"<[^>]+>")
_EMOJI = re.compile(r"\{emoji:[^}]*\}")
_ANON = re.compile(r"&[^&\s]*&")


def has_placeholder(s: str) -> bool:
    return bool(_TAG.search(s) or _EMOJI.search(s) or _ANON.search(s))


class ECPair(NamedTuple):
    doc_id: str
    unit_id: str
    original: str
    corrected: str


def ec_files(data_dir: Path) -> list[Path]:
    return sorted(Path(data_dir).glob("NIKL_EC_*/*.json"))


def iter_ec_pairs(paths: list[Path]) -> Iterator[ECPair]:
    """맞춤법 교정 말뭉치의 (원문, 교정문) 쌍. 단위 키는 'utterance' 또는 'paragraph'."""
    for p in paths:
        with open(p, "rb") as f:
            for doc in ijson.items(f, "document.item"):
                doc_id = doc.get("id", "")
                units = doc.get("utterance") or doc.get("paragraph") or []
                for u in units:
                    o = u.get("original_form")
                    c = u.get("corrected_form")
                    if o is not None and c is not None:
                        yield ECPair(doc_id, u.get("id", ""), o, c)


def mp_files(data_dir: Path) -> list[Path]:
    return sorted(Path(data_dir).glob("NIKL_MP_*/*.json"))


def iter_mp_sentences(paths: list[Path]) -> Iterator[tuple[str, str]]:
    """형태 분석 말뭉치의 (문장 id, 문장 form)."""
    for p in paths:
        with open(p, "rb") as f:
            for doc in ijson.items(f, "document.item"):
                for s in doc.get("sentence") or []:
                    form = s.get("form")
                    if form:
                        yield s.get("id", ""), form
