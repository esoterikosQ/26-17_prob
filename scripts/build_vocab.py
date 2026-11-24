"""디코더 어휘: 교정문·신문의 어절 핵심 빈도.

    python scripts/build_vocab.py      # → datasets/vocab/eojeol_freq.tsv (빈도 내림차순, git 제외)

출처: EC train 교정문, Kor-* train 교정문, 신문 2024·2025 문단(태그 제거). 어절 = 공백 단위,
앞뒤 부호를 떼고 핵심만 센다(hnc.surface.split_punct). dev·test는 쓰지 않는다.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.surface import split_punct  # noqa: E402

_TAG = re.compile(r"<[^>]+>")


def main() -> None:
    import ijson
    out = Path("datasets/vocab")
    out.mkdir(parents=True, exist_ok=True)
    c: Counter = Counter()
    stats: Counter = Counter()
    t0 = time.time()

    def add(text: str, src: str) -> None:
        for tok in text.split():
            core = split_punct(tok)[1]
            if core:
                c[core] += 1
                stats[src] += 1

    for p in [Path("datasets/ec/train.jsonl")] + sorted(Path("datasets/kor_gec").glob("*_train.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                add(json.loads(line)["y"], "corrected")
    for p in sorted(Path("data").glob("NIKL_NEWSPAPER_*/**/*.json")):
        with open(p, "rb") as f:
            for form in ijson.items(f, "document.item.paragraph.item.form"):
                add(_TAG.sub(" ", form), "news")
    with open(out / "eojeol_freq.tsv", "w", encoding="utf-8") as f:
        for w, n in c.most_common():
            f.write(f"{w}\t{n}\n")
    stats.update({"types": len(c), "types_ge5": sum(1 for n in c.values() if n >= 5),
                  "seconds": round(time.time() - t0)})
    print(json.dumps(dict(stats), ensure_ascii=False))


if __name__ == "__main__":
    main()
