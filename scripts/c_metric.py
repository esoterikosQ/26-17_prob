"""C-1 평가 전용: 단일 주석인 Kor-Learner dev의 공식 M² 편집 그래프에서 후보별 충분통계를 캐시."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import Counts, oracle, surface  # noqa: E402
from scripts.decode import m2_blocks  # noqa: E402
from scripts.population_dev import is_selection  # noqa: E402

DEV = Path("datasets/kor_gec/korean_learner_dev.jsonl")
GOLD = Path("data/kor_gec/korean_learner/korean_learner_val.m2")


def annotations(block):
    source = surface(block[0][2:])
    types, edits = [], []
    for line in block[1:]:
        pos, typ, correction, _, _, annotator = line[2:].split("|||")
        if annotator != "0":
            raise ValueError("이 충분통계 오라클은 단일 주석 전용이다")
        if typ == "noop":
            continue
        a, b = map(int, pos.split())
        if not 0 <= a <= b <= len(source.split()):
            raise ValueError("잘못된 M² 범위")
        corrs = [c.strip() if c != "-NONE-" else "" for c in correction.split("||")]
        edits.append((a, b, " ".join(source.split()[a:b]), corrs))
        types.append(typ)
    return source, types, edits


def per_candidate(text, source, gold):
    import levenshtein as lev  # tools/m2scorer/scripts, 기존에 허용·설치된 공식 구현
    a, b = source.split(), text.split()
    if a == b:
        return {"text": text, "tp": [], "proposed": 0}
    graphs = [lev.edit_graph(*lev.levenshtein_matrix(a, b, 1, 1, cost)) for cost in (1, 2)]
    v1, e1, d1, t1 = graphs[0]
    v2, e2, d2, t2 = graphs[1]
    vertices, edges, dist, table = lev.merge_graph(v1, v2, e1, e2, d1, d2, t1, t2)
    vertices, edges, dist, table = lev.transitive_arcs(vertices, edges, dist, table, 2)
    adjusted = lev.set_weights(edges, dist, table, gold)
    predicted = lev.best_edit_seq_bf(vertices, edges, adjusted, table)
    correct = lev.matchSeq(predicted, gold)
    indices, start = [], 0
    for e in correct:
        k = next(i for i in range(start, len(gold)) if lev.matchEdit(e, gold[i]))
        indices.append(k)
        start = k + 1
    if len(indices) > len(predicted):
        raise ValueError("M² 일치 편집 수가 제안 수보다 많다")
    return {"text": text, "tp": indices, "proposed": len(predicted)}


def stats(row, h):
    return Counts(len(h["tp"]), h["proposed"], row["ngold"])


def work(item):
    pool, r, block = item
    x, _, gold = annotations(block)
    if pool["unit_id"] != r["unit_id"] or pool["x"] != r["x"] or surface(r["x"]) != x:
        raise ValueError("M² 캐시 원문 불일치")
    return {"i": pool["i"], "ngold": len(gold),
            "hyps": [dict(per_candidate(h["text"], x, gold), h=h["h"], s=h["s"]) for h in pool["hyps"]]}


def proposal_evidence(metric, recs, indices):
    exact = dict.fromkeys(("both", "h_only", "s_only", "neither"), 0)
    hits = dict.fromkeys(exact, 0)
    groups = {"h": [], "s": [], "union": []}
    n_h = n_s = n_u = overlap = 0
    for i in indices:
        row, y = metric[i], surface(recs[i]["y"])
        hyp = row["hyps"]
        h, s = [c for c in hyp if c["h"] is not None], [c for c in hyp if c["s"] is not None]
        n_h += len(h)
        n_s += len(s)
        n_u += len(hyp)
        overlap += sum(c["h"] is not None and c["s"] is not None for c in hyp)
        eh, es = any(c["text"] == y for c in h), any(c["text"] == y for c in s)
        exact["both" if eh and es else "h_only" if eh else "s_only" if es else "neither"] += 1
        gh, gs = {k for c in h for k in c["tp"]}, {k for c in s for k in c["tp"]}
        hits["both"] += len(gh & gs)
        hits["h_only"] += len(gh - gs)
        hits["s_only"] += len(gs - gh)
        hits["neither"] += row["ngold"] - len(gh | gs)
        for label, candidates in zip(groups, (h, s, hyp)):
            groups[label].append([stats(row, c) for c in candidates])
    return {"n": len(indices), "h": n_h, "s": n_s, "union": n_u, "overlap": overlap,
            "gold_sentence": exact, "gold_edit": hits,
            "oracle_m2": {k: oracle(v).metrics() for k, v in groups.items()}}


def main(out: Path, workers: int):
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    rows = [json.loads(line) for line in (out / "pool.jsonl").read_text().splitlines()]
    recs = [json.loads(line) for line in DEV.read_text().splitlines()]
    blocks = m2_blocks(GOLD)
    if len(rows) != len(recs) or len(rows) != len(blocks) or any(r["i"] != i for i, r in enumerate(rows)):
        raise ValueError("완전한 dev 후보만 평가한다")
    with mp.get_context("fork").Pool(workers) as pool:
        metric = list(pool.imap(work, zip(rows, recs, blocks, strict=True), chunksize=8))
    with (out / "m2_stats.jsonl").open("x", encoding="utf-8") as f:
        for r in metric:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    check = [i for i, r in enumerate(recs) if not is_selection("korean_learner", r)]
    ev = {"all_dev": proposal_evidence(metric, recs, range(len(rows))),
          "check": proposal_evidence(metric, recs, check)}
    (out / "proposal_evidence.json").write_text(json.dumps(ev, indent=1))
    print(json.dumps(ev), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    main(args.out, args.workers)
