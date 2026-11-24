"""Kor-* 데이터셋 구축: 쌍 분류(A-0c 규칙) + KAGAS 편집 결합.

    python scripts/build_kor_gec.py      # data/kor_gec → datasets/kor_gec/<name>_<split>.jsonl

레코드 필드는 EC 데이터셋과 같다(doc_id, unit_id, src, genre, device, keyboard, x, y, cat) +
``kagas``: [[시작 어절, 끝 어절, 유형, 교정], ...] (M² S 줄이 원문과 다르면 null).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.ec_data import CATEGORIES, classify  # noqa: E402
from hnc.kor_gec import DATASETS, SPLITS, iter_m2, read_pairs, same_sentence  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/kor_gec"))
    ap.add_argument("--out", type=Path, default=Path("datasets/kor_gec"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    stats = {}
    kagas_types: dict = defaultdict(Counter)
    for name in DATASETS:
        for split, fsplit in SPLITS.items():
            pairs = read_pairs(args.data / name / f"{name}_{fsplit}.txt")
            m2 = list(iter_m2(args.data / name / f"{name}_{fsplit}.m2"))
            cats: Counter = Counter()
            m2_ok = 0
            with open(args.out / f"{name}_{split}.jsonl", "w", encoding="utf-8") as f:
                for i, (x, y) in enumerate(pairs):
                    kagas = None
                    if i < len(m2) and same_sentence(m2[i].source, x):
                        kagas = [list(e) for e in m2[i].edits]
                        m2_ok += 1
                        for e in m2[i].edits:
                            kagas_types[f"{name}_{split}"][e[2]] += 1
                    cat = classify(x, y, check_placeholder=False)
                    cats[cat] += 1
                    rec = {"doc_id": name, "unit_id": f"{name}.{split}.{i}", "src": name, "genre": name,
                           "device": "", "keyboard": "", "x": x, "y": y, "cat": cat, "kagas": kagas}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats[f"{name}_{split}"] = {"pairs": len(pairs), "m2_sentences": len(m2), "m2_matched": m2_ok,
                                        **{c: cats[c] for c in CATEGORIES if cats[c]}}
    print(json.dumps({"datasets": stats}, ensure_ascii=False))
    print(json.dumps({"kagas_types": {k: dict(v.most_common()) for k, v in kagas_types.items()}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
