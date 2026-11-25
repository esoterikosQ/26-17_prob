"""A-1a 학습 편집쌍 추출·계수.

    python scripts/extract_edits.py --sources ec2,native --tag ec2_native --workers 12

소스:
- ``ec2``   : EC train 중 자판이 2벌식(쿼티)인 쌍 (plan.md §4-5: 2벌식부터)
- ``ec``    : EC train 전체
- ``native``: Kor-Native train
- ``learner``·``lang8``: Kor-Learner·Kor-Lang8 train (학습자 집단 채널, A-3)
- ``learnerJ``·``lang8J``: 위 둘에서 형태소 층위 편집을 되돌린 자모 층위 쌍(A-3d, ``make_jamo_only_pairs.py``)
- ``ec2dev``·``nativedev``: 위 둘의 dev (A-1c held-out 우도용; 계수표가 아니라 창만 쓴다)
모든 범주(동일·부호·띄어쓰기·내용)를 처리한다 — 오류 없는 위치도 분모(부분열 빈도)에 들어가야
오류율이 과대평가되지 않는다. 출력은 datasets/channel/<tag>/ (git 제외):
- edits.jsonl        : {"a": α, "b": β, "c": 문맥 확장 포함 계수, "core": 문맥 없는 핵심 편집 계수}
- alpha_counts.jsonl : edits의 α들·모든 단일 자모·()의 y 쪽 부분열 빈도
- windows.jsonl      : A-1c EM용 덩어리 창 {"y": y 창, "x": x 창, "c": 빈도}
- stats.json         : 쌍·덩어리 분류·편집 종류 수·핵심 편집 상위 목록
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.align import Span, align_pair  # noqa: E402
from hnc import jamo  # noqa: E402
from hnc.channel_edits import atom_windows, sentence_ops, substring_counts, substring_edits  # noqa: E402

SOURCES = {
    "ec2": (Path("datasets/ec/train.jsonl"), lambda r: r["keyboard"] == "2벌식(쿼티)"),
    "ec": (Path("datasets/ec/train.jsonl"), lambda r: True),
    "native": (Path("datasets/kor_gec/native_train.jsonl"), lambda r: True),
    "learner": (Path("datasets/kor_gec/korean_learner_train.jsonl"), lambda r: True),
    "lang8": (Path("datasets/kor_gec/lang8_train.jsonl"), lambda r: True),
    "learnerJ": (Path("datasets/kor_gec/korean_learner_jamo_train.jsonl"), lambda r: True),
    "lang8J": (Path("datasets/kor_gec/lang8_jamo_train.jsonl"), lambda r: True),
    "ec2dev": (Path("datasets/ec/dev.jsonl"), lambda r: r["keyboard"] == "2벌식(쿼티)"),
    "nativedev": (Path("datasets/kor_gec/native_dev.jsonl"), lambda r: True),
}
_ANA = None


def _init():
    global _ANA
    from hnc.morph import MorphAnalyzer
    _ANA = MorphAnalyzer(num_workers=1)


def _work(batch):
    edits, core, alpha, st, win = Counter(), Counter(), Counter(), Counter(), Counter()
    spans = _ANA.spans_many([y for y, _ in batch])
    for (y, x), sp in zip(batch, spans):
        regions = align_pair(y, x, [Span(*s) for s in sp])
        ops = sentence_ops(y, regions)
        st["pairs"] += 1
        st["pairs_with_edit"] += any(op != "=" for op, _, _ in ops)
        for a, b, is_core in substring_edits(ops, stats=st):
            edits[(a, b)] += 1
            if is_core:
                core[(a, b)] += 1
        alpha.update(substring_counts([s for _, s, _ in ops if s]))
        win.update(atom_windows(ops))
    return edits, core, alpha, st, win


def _batches(sources, size):
    batch = []
    for name in sources:
        path, keep = SOURCES[name]
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if keep(r):
                    batch.append((r["y"], r["x"]))
                    if len(batch) >= size:
                        yield batch
                        batch = []
    if batch:
        yield batch


def _fmt(sym):
    return " ".join(sym) if sym else "∅"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="ec2,native")
    ap.add_argument("--tag", default="ec2_native")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--batch", type=int, default=2000)
    args = ap.parse_args()
    out = Path("datasets/channel") / args.tag
    out.mkdir(parents=True, exist_ok=True)
    sources = args.sources.split(",")

    t0 = time.time()
    edits, core, alpha, st, win = Counter(), Counter(), Counter(), Counter(), Counter()
    with mp.get_context("fork").Pool(args.workers, initializer=_init) as pool:
        for e, c, a, s, w in pool.imap_unordered(_work, _batches(sources, args.batch)):
            edits.update(e)
            core.update(c)
            alpha.update(a)
            st.update(s)
            win.update(w)

    with open(out / "edits.jsonl", "w", encoding="utf-8") as f:
        for (a, b), c in edits.most_common():
            f.write(json.dumps({"a": list(a), "b": list(b), "c": c, "core": core.get((a, b), 0)},
                               ensure_ascii=False) + "\n")
    alphas = {a for a, _ in edits} | {()} | {(s,) for s in jamo.ALPHABET}
    with open(out / "alpha_counts.jsonl", "w", encoding="utf-8") as f:
        for a in sorted(alphas, key=lambda t: (len(t), t)):
            f.write(json.dumps({"a": list(a), "c": alpha.get(a, 0)}, ensure_ascii=False) + "\n")
    with open(out / "windows.jsonl", "w", encoding="utf-8") as f:
        for (y, x), c in win.most_common():
            f.write(json.dumps({"y": list(y), "x": list(x), "c": c}, ensure_ascii=False) + "\n")
    stats = {"sources": sources, **dict(st), "edit_types": len(edits), "core_types": len(core),
             "window_types": len(win), "windows": sum(win.values()),
             "alpha_types": len(alphas), "seconds": round(time.time() - t0, 1),
             "core_top": [[_fmt(a), _fmt(b), c, round(c / max(alpha.get(a, 1), 1), 6)]
                          for (a, b), c in core.most_common(60)]}
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "core_top"}, ensure_ascii=False))
    for row in stats["core_top"][:40]:
        print("\t".join(map(str, row)))


if __name__ == "__main__":
    main()
