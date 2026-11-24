"""EC dev 정답 M² 생성: 2벌식 표본 → KAGAS(tools/KAGAS, .venv-kagas).

    python scripts/make_ec_gold.py --n 5000 --seed 0 --tag ec2_dev5k

출력 datasets/ec_gold/<tag>/ (git 제외): records.jsonl(표본 레코드, 디코더 입력), orig.txt, cor.txt,
gold.m2(KAGAS), stats.json. 범주는 가리지 않는다(고칠 필요 없는 문장도 실제 비율대로 포함).
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import time
from collections import Counter
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tag", default="ec2_dev5k")
    args = ap.parse_args()
    out = Path("datasets/ec_gold") / args.tag
    out.mkdir(parents=True, exist_ok=True)

    recs = []
    with open("datasets/ec/dev.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["keyboard"] == "2벌식(쿼티)":
                r["x"] = " ".join(r["x"].split())
                r["y"] = " ".join(r["y"].split())
                if r["x"] and r["y"]:
                    recs.append(r)
    recs = random.Random(args.seed).sample(recs, min(args.n, len(recs)))
    with open(out / "records.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "orig.txt").write_text("\n".join(r["x"] for r in recs) + "\n", encoding="utf-8")
    (out / "cor.txt").write_text("\n".join(r["y"] for r in recs) + "\n", encoding="utf-8")

    t0 = time.time()
    kagas = Path("tools/KAGAS")
    with open(out / "kagas.log", "w", encoding="utf-8") as log:
        subprocess.run([str(Path(".venv-kagas/bin/python").resolve()), "parallel_to_m2_korean.py",
                        "-orig", str((out / "orig.txt").resolve()), "-cor", str((out / "cor.txt").resolve()),
                        "-out", str((out / "gold.m2").resolve()), "-hunspell", "./aff-dic", "-noprint"],
                       cwd=kagas, stdout=log, stderr=subprocess.STDOUT, check=True)
    blocks = (out / "gold.m2").read_text(encoding="utf-8").count("\nS ") + 1
    types: Counter = Counter()
    for line in (out / "gold.m2").read_text(encoding="utf-8").splitlines():
        if line.startswith("A "):
            types[line.split("|||")[1]] += 1
    stats = {"n": len(recs), "m2_blocks": blocks, "kagas_sec": round(time.time() - t0, 1),
             "cats": dict(Counter(r["cat"] for r in recs)), "types": dict(types.most_common())}
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
