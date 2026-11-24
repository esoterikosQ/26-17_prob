"""A-2 형태소 LM 학습 자료 구축 (Kiwi 분석 → ``▁형태/품사`` 토큰 문장).

    python scripts/build_lm_corpus.py --tag v1 --workers 16 [--news-limit N]

학습(train.txt): EC train 교정문, Kor-Learner·Native·Lang8 train 교정문, 신문 말뭉치 2024·2025
(문단을 Kiwi로 문장 분리). 평가(dev_<이름>.txt): EC dev(2벌식)·Kor-* dev 교정문 — 학습에 쓰지 않음.
출력 datasets/lm/<tag>/ (git 제외).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.morph_lm import kiwi_morphs, lm_tokens  # noqa: E402

_TAG = re.compile(r"<[^>]+>")


def _jsonl_y(path: Path, keep=lambda r: True):
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if keep(r) and r["y"].strip():
                yield r["y"]


def _news_paragraphs(data: Path, limit: int):
    import ijson
    n = 0
    for p in sorted(data.glob("NIKL_NEWSPAPER_*/**/*.json")):
        with open(p, "rb") as f:
            for form in ijson.items(f, "document.item.paragraph.item.form"):
                form = " ".join(_TAG.sub(" ", form).split())
                if form:
                    yield form
                    n += 1
                    if limit and n >= limit:
                        return


def _batches(it, size):
    batch = []
    for x in it:
        batch.append(x)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v1")
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--news-limit", type=int, default=0, help="신문 문단 수 제한(0=전체)")
    ap.add_argument("--no-news", action="store_true", help="신문 말뭉치를 쓰지 않음")
    ap.add_argument("--batch", type=int, default=20000)
    args = ap.parse_args()
    out = Path("datasets/lm") / args.tag
    out.mkdir(parents=True, exist_ok=True)

    from kiwipiepy import Kiwi
    kiwi = Kiwi(num_workers=args.workers)
    stats: Counter = Counter()
    t0 = time.time()

    def write_units(f, texts, name):
        for batch in _batches(texts, args.batch):
            for text, toks in zip(batch, kiwi.tokenize(batch)):
                if toks:
                    f.write(" ".join(lm_tokens(kiwi_morphs(text, toks))) + "\n")
                    stats[f"{name}_sents"] += 1
                    stats[f"{name}_tokens"] += len(toks)

    kor = ["korean_learner", "native", "lang8"]
    with open(out / "train.txt", "w", encoding="utf-8") as f:
        write_units(f, _jsonl_y(Path("datasets/ec/train.jsonl")), "ec_train")
        for name in kor:
            write_units(f, _jsonl_y(Path(f"datasets/kor_gec/{name}_train.jsonl")), f"{name}_train")
        news = [] if args.no_news else _news_paragraphs(args.data, args.news_limit)
        for batch in _batches(news, args.batch):
            for para, sents in zip(batch, kiwi.split_into_sents(batch, return_tokens=True)):
                stats["news_paragraphs"] += 1
                for s in sents:
                    if s.tokens:
                        f.write(" ".join(lm_tokens(kiwi_morphs(para, s.tokens))) + "\n")
                        stats["news_sents"] += 1
                        stats["news_tokens"] += len(s.tokens)
            print(json.dumps({"progress_news_paragraphs": stats["news_paragraphs"],
                              "sec": round(time.time() - t0)}), flush=True)

    dev_sets = {"ec2": (Path("datasets/ec/dev.jsonl"), lambda r: r["keyboard"] == "2벌식(쿼티)")}
    dev_sets.update({n: (Path(f"datasets/kor_gec/{n}_dev.jsonl"), lambda r: True) for n in kor})
    for name, (path, keep) in dev_sets.items():
        with open(out / f"dev_{name}.txt", "w", encoding="utf-8") as f:
            write_units(f, _jsonl_y(path, keep), f"dev_{name}")

    stats["seconds"] = round(time.time() - t0)
    (out / "stats.json").write_text(json.dumps(dict(stats), ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(dict(stats), ensure_ascii=False))


if __name__ == "__main__":
    main()
