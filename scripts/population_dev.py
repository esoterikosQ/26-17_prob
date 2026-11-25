"""A-3g: dev에서 집단 채널·형태소 경로 가중치를 선택 절반에서 고르고 확인 절반에 한 번 적용.

    python scripts/population_dev.py --tag a3g_pop_v1 --workers 12

ρ는 학습자 자모 채널의 비율, w는 형태소 편집을 포함하는 경로의 질량 배수이다.
모든 조합에 같은 학습자 형태소 표, 보간 LM, 빔, 자료 순서를 쓴다. 신경 재채점·test는 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.kor_gec import same_sentence  # noqa: E402
from scripts.decode import m2_blocks, m2score, write_m2  # noqa: E402

DOMAINS = ("korean_learner", "native", "ec2")
SHARES = (0.0, 0.5, 1.0)
MASSES = (0.0, 0.1, 1.0, 3.0)
NATIVE = "datasets/channel/ec2_native_em"
LEARNER = "datasets/channel/learner_lang8_J"
MORPH = "datasets/morph_channel/learner"
LM = "datasets/lm/v0/lm5.bin,datasets/lm/v1/lm5.bin"
SEED = "hnc-pop-v1:"


def data_paths(domain: str) -> tuple[Path, Path]:
    if domain == "ec2":
        d = Path("datasets/ec_gold/ec2_dev5k")
        return d / "records.jsonl", d / "gold.m2"
    return (Path("datasets/kor_gec") / f"{domain}_dev.jsonl",
            Path("data/kor_gec") / domain / f"{domain}_val.m2")


def is_selection(domain: str, record: dict) -> bool:
    group = record["doc_id"] if domain == "ec2" else " ".join(record["y"].split())
    return hashlib.sha256((SEED + group).encode("utf-8")).digest()[0] < 128


def key(share: float, mass: float) -> str:
    return f"r{share:g}_m{mass:g}"


def run_decode(domain: str, root: Path, name: str, share: float, *, bm: bool, workers: int) -> None:
    directory = root / domain / name
    directory.mkdir(parents=True, exist_ok=False)
    args = [sys.executable, "scripts/decode.py", "--set", domain, "--split", "dev",
            "--tag", str(directory.relative_to("outputs/a1d")), "--workers", str(workers),
            "--lm", LM, "--lm-weights", "0.8,0.2", "--lam-len", "0", "--lam-lm", "0.5" if domain == "native" else "1",
            "--channel", "datasets/channel/ec2_native" if bm else NATIVE]
    if bm:
        args.extend(["--morph-weight", "0"])
    else:
        args.extend(["--mix-channel", LEARNER, "--mix-weight", str(share), "--morph-channel", MORPH,
                     "--morph-max-cands", "30", "--morph-weight", ",".join(map(str, MASSES))])
    if domain == "ec2":
        rec, gold = data_paths(domain)
        args.extend(["--records", str(rec), "--gold-m2", str(gold)])
    print(f"{domain}/{name}: decoding", flush=True)
    with (directory / "run.log").open("w", encoding="utf-8") as log:
        subprocess.run(args, check=True, stdout=log, stderr=subprocess.STDOUT)


def evaluate(domain: str, directory: Path, workers: int) -> dict:
    rp, gp = data_paths(domain)
    with rp.open(encoding="utf-8") as src:
        records = [json.loads(line) for line in src]
    gold = m2_blocks(gp)
    if len(records) != len(gold) or not all(same_sentence(r["x"], g[0][2:]) for r, g in zip(records, gold)):
        raise ValueError(f"{domain}: 원문과 정답 M² 순서가 일치하지 않는다")
    select = [is_selection(domain, r) for r in records]
    groups = {"select": [i for i, flag in enumerate(select) if flag],
              "check": [i for i, flag in enumerate(select) if not flag]}
    if domain == "native":
        with Path("datasets/kor_gec/native_train.jsonl").open(encoding="utf-8") as src:
            seen = {" ".join(json.loads(line)["y"].split()) for line in src}
        novel = [i for i, r in enumerate(records) if " ".join(r["y"].split()) not in seen]
        groups["novel_all"] = novel
        groups["novel_check"] = [i for i in novel if not select[i]]
    if not all(groups.values()):
        raise ValueError(f"{domain}: 빈 평가 부분집합")

    reference = {"native": key(0, 0), "korean_learner": key(1, 1), "ec2": "bm"}[domain]
    models = {}
    runs = [f"r{v:g}" for v in SHARES] + (["bm"] if domain == "ec2" else [])
    for run in runs:
        d = directory / run
        report = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        sources = (d / "source.txt").read_text(encoding="utf-8").splitlines()
        if len(sources) != len(gold) or not all(same_sentence(x, r["x"]) for x, r in zip(sources, records)):
            raise ValueError(f"{d}: 가설의 문장 순서가 다르다")
        for row in report["results"]:
            label = "bm" if run == "bm" else key(report["mix_weight"], row["morph_weight"])
            pred = (d / row["hyp"]).read_text(encoding="utf-8").splitlines()
            if len(pred) != len(records) or label in models:
                raise ValueError(f"{d}: 중복 설정 또는 가설 수 불일치")
            models[label] = (pred, row["all"])
    grid = [key(r, m) for r in SHARES for m in MASSES]
    if not set(grid + [reference]) <= models.keys():
        raise ValueError("격자 또는 기준 모형의 결과가 없다")

    with TemporaryDirectory(dir=directory) as tmp:
        td = Path(tmp)
        for part, indices in groups.items():
            write_m2([gold[i] for i in indices], td / f"{part}.m2")

        def score(label: str, part: str) -> dict:
            pred = models[label][0]
            hp = td / f"{label}_{part}.txt"
            hp.write_text("".join(pred[i] + "\n" for i in groups[part]), encoding="utf-8")
            return m2score(hp, td / f"{part}.m2")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            tune = dict(zip(models, pool.map(lambda label: score(label, "select"), models)))
        # 4자리 동점이면 기존 설정을 우선하고 이후에는 사전에 선언한 격자 순서로 고른다.
        chosen = max(grid, key=lambda label: (tune[label]["F0.5"], label == reference))
        comparisons = {}
        for label in dict.fromkeys((reference, chosen)):
            comparisons[label] = {"all_dev": models[label][1], "select": tune[label],
                                  **{part: score(label, part) for part in groups if part != "select"}}

    return {"domain": domain, "dev": len(records), "sizes": {p: len(v) for p, v in groups.items()},
            "group": "doc_id" if domain == "ec2" else "normalized_y", "hash_seed": SEED,
            "reference": reference, "selected": chosen, "selection_grid": tune, "comparison": comparisons}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="a3g_pop_v1")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--domains", nargs="+", choices=DOMAINS, default=DOMAINS)
    ap.add_argument("--score-only", action="store_true", help="다시 디코딩하지 않고 등록된 dev 출력만 채점")
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.tag) or args.workers < 1:
        ap.error("새 출력 태그와 양의 workers가 필요하다")
    root = Path("outputs/a1d") / args.tag
    for domain in args.domains:
        if not args.score_only:
            for share in SHARES:
                run_decode(domain, root, f"r{share:g}", share, bm=False, workers=args.workers)
            if domain == "ec2":
                run_decode(domain, root, "bm", 0, bm=True, workers=args.workers)
        result = evaluate(domain, root / domain, args.workers)
        (root / domain / "comparison.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(json.dumps({k: result[k] for k in ("domain", "reference", "selected", "comparison")}), flush=True)


if __name__ == "__main__":
    main()
