"""Kor-Learner·Kor-Native·Kor-Lang8 (Yoon et al. 2023) 리더.

자료는 itcerdo `~/projects/phdq_bart/data/Preprocessed/`에서 사용자 허락을 받아 `data/kor_gec/`로
복사했다(2026-11-24). 분할은 저자 배포 그대로 쓴다(train / val / test → train / dev / test).

- ``<name>/<name>_<split>.txt``: ``오류문 \\t 교정문`` (헤더 없음)
- ``<name>/<name>_<split>.m2``: KAGAS가 만든 M². ``S`` 줄은 오류문(어절 단위), ``A`` 줄은
  ``시작 끝|||유형|||교정|||REQUIRED|||-NONE-|||주석자``. 유형(PARTICLE, ENDING, CONJUGATION, WS,
  SPELL …)은 자동 주석으로, 오류유형 z의 지도 신호로 쓸 수 있다(사람 주석 아님).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, List, NamedTuple, Tuple

DATASETS = ("korean_learner", "native", "lang8")
SPLITS = {"train": "train", "dev": "val", "test": "test"}


class M2Sentence(NamedTuple):
    source: str
    edits: List[Tuple[int, int, str, str]]   # (시작 어절, 끝 어절, 유형, 교정 문자열)


def read_pairs(path: Path) -> List[Tuple[str, str]]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            src, _, tgt = line.partition("\t")
            out.append((src, tgt))
    return out


def parse_m2(lines: Iterable[str]) -> Iterator[M2Sentence]:
    """M² 줄 → 문장별 (원문, 편집). ``noop`` 편집은 버린다."""
    src, edits = None, []
    for line in lines:
        line = line.rstrip("\n")
        if line.startswith("S "):
            if src is not None:
                yield M2Sentence(src, edits)
            src, edits = line[2:], []
        elif line.startswith("A "):
            fields = line[2:].split("|||")
            start, end = (int(v) for v in fields[0].split())
            if fields[1] != "noop":
                edits.append((start, end, fields[1], fields[2]))
        elif not line.strip() and src is not None:
            yield M2Sentence(src, edits)
            src, edits = None, []
    if src is not None:
        yield M2Sentence(src, edits)


def iter_m2(path: Path) -> Iterator[M2Sentence]:
    with open(path, encoding="utf-8") as f:
        yield from parse_m2(f)


def same_sentence(a: str, b: str) -> bool:
    return a.split() == b.split()
