"""정답 중복 부분집합별 평가: dev 문장을 "정답 y가 train 정답에 그대로 있음(겹침)"과 "없음(새 문장)"으로
나눠 같은 가설 파일을 다시 채점한다.

    python scripts/split_eval.py --set native --dirs outputs/a1d/native_v1_em outputs/a1d/native_interp08_em

- Kor-Native는 공식 분할에서 dev 정답의 67%가 train 정답과 같은 문장(오류문 x만 다름)이다. 영역 내 LM이
  train 정답을 외우면 겹침 부분에서 점수가 부풀므로, 새 문장 부분의 점수를 따로 본다.
- 디렉토리마다 ``gold_all.m2``·``gold_a1.m2``와 ``hyp_*.txt``(decode.py·rescore.py 출력)를 쓴다.
- 출력: 표준출력(JSON), 각 디렉토리의 ``split_eval.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.decode import m2_blocks, m2score, write_m2  # noqa: E402


def norm(s: str) -> str:
    return " ".join(s.split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="native")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--records", type=Path, default=None)
    ap.add_argument("--train", type=Path, default=None)
    ap.add_argument("--dirs", nargs="+", type=Path, required=True)
    ap.add_argument("--hyps", default="", help="채점할 가설 파일 이름(쉼표; 기본: hyp_*.txt 전부)")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    rec = args.records or Path(f"datasets/kor_gec/{args.set}_{args.split}.jsonl")
    train = args.train or Path(f"datasets/kor_gec/{args.set}_train.jsonl")
    seen = {norm(json.loads(line)["y"]) for line in open(train, encoding="utf-8")}
    ys = [norm(json.loads(line)["y"]) for line in open(rec, encoding="utf-8")]
    subsets = {"overlap": [i for i, y in enumerate(ys) if y in seen],
               "novel": [i for i, y in enumerate(ys) if y not in seen]}
    print(json.dumps({"sents": len(ys), **{k: len(v) for k, v in subsets.items()}}), flush=True)

    jobs = []
    for d in args.dirs:
        sd = d / "split_eval"
        sd.mkdir(exist_ok=True)
        gold = {g: m2_blocks(d / f"gold_{g}.m2") for g in ("all", "a1")}
        assert len(gold["all"]) == len(ys), (d, len(gold["all"]), len(ys))
        for name, idx in subsets.items():
            for g, blocks in gold.items():
                write_m2([blocks[i] for i in idx], sd / f"gold_{g}_{name}.m2")
        hyps = [d / h for h in args.hyps.split(",") if h] or sorted(d.glob("hyp_*.txt"))
        for h in hyps:
            lines = h.read_text(encoding="utf-8").splitlines()
            assert len(lines) == len(ys), (h, len(lines))
            for name, idx in subsets.items():
                hp = sd / f"{h.stem}_{name}.txt"
                hp.write_text("\n".join(lines[i] for i in idx) + "\n", encoding="utf-8")
                for g in ("all", "a1"):
                    jobs.append((str(d), h.stem, name, g, hp, sd / f"gold_{g}_{name}.m2"))

    with ThreadPoolExecutor(args.workers) as ex:
        scores = list(ex.map(lambda j: m2score(j[4], j[5]), jobs))
    out = {}
    for (d, h, name, g, _, _), s in zip(jobs, scores):
        out.setdefault(d, {}).setdefault(h, {}).setdefault(name, {})[g] = s
    for d, v in out.items():
        (Path(d) / "split_eval.json").write_text(json.dumps(v, indent=1), encoding="utf-8")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
