"""A-0c EC 데이터셋 구축: 쌍 분류 + 문서 단위 고정 분할.

    python scripts/build_ec.py            # datasets/ec/{train,dev,test}.jsonl + 통계(표준출력)

출력 레코드: doc_id, unit_id, src(파일), genre(web|messenger), device, keyboard, x, y, cat.
empty·placeholder 범주는 쓰지 않고 개수만 센다. datasets/ 는 git 제외.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.corpus import ec_files, iter_ec_pairs  # noqa: E402
from hnc.ec_data import CATEGORIES, classify, split_of  # noqa: E402

DROP = {"empty", "placeholder"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("datasets/ec"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    outs = {s: open(args.out / f"{s}.jsonl", "w", encoding="utf-8") for s in ("train", "dev", "test")}
    by_split: dict = defaultdict(Counter)
    by_genre: dict = defaultdict(Counter)
    by_keyboard: dict = defaultdict(Counter)
    docs: dict = defaultdict(set)

    for path in ec_files(args.data):
        genre = "messenger" if path.name.startswith("MX") else "web"
        for p in iter_ec_pairs([path]):
            x = p.form or p.original
            cat = classify(x, p.corrected)
            split = split_of(p.doc_id)
            by_split[split][cat] += 1
            by_genre[genre][cat] += 1
            if cat in DROP:
                continue
            by_keyboard[p.keyboard or "(없음)"][cat] += 1
            docs[split].add(p.doc_id)
            rec = {"doc_id": p.doc_id, "unit_id": p.unit_id, "src": path.stem, "genre": genre,
                   "device": p.device, "keyboard": p.keyboard, "x": x, "y": p.corrected, "cat": cat}
            outs[split].write(json.dumps(rec, ensure_ascii=False) + "\n")
    for f in outs.values():
        f.close()

    def row(c: Counter) -> dict:
        return {k: c[k] for k in CATEGORIES} | {"total": sum(c.values())}

    print(json.dumps({"split": {s: row(c) for s, c in sorted(by_split.items())}}, ensure_ascii=False))
    print(json.dumps({"docs_kept": {s: len(d) for s, d in sorted(docs.items())}}, ensure_ascii=False))
    print(json.dumps({"genre": {g: row(c) for g, c in sorted(by_genre.items())}}, ensure_ascii=False))
    kb = sorted(by_keyboard.items(), key=lambda kv: -sum(kv[1].values()))
    print(json.dumps({"keyboard": {k: row(c) for k, c in kb}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
