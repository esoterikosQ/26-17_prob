"""C-3/C-4: dev 선택 부분에서만 λ₄ 결정, 공통 후보 점수 절제 및 모든 변경의 설명 감사."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import aggregate, surface  # noqa: E402
from scripts.c_metric import DEV, GOLD, annotations, stats  # noqa: E402
from scripts.decode import m2_blocks, m2score, write_m2  # noqa: E402
from scripts.population_dev import is_selection  # noqa: E402

GRID = (0., .25, .5, 1., 2., 4., 8.)
HNC = Path("outputs/rescore/learner_JJ_Ml30")
FROZEN_B = HNC / "hyp_lm0.5_p0.0_len0.0_pko131.0_rbl0.0.txt"
GOLD_FAMILY = {"SPELL": "JAMO", "SHORTEN": "JAMO", "CONJUGATION": "JAMO", "WS": "WS",
               "PARTICLE": "PARTICLE", "ENDING": "ENDING", "NOUN": "LEXICAL", "VERB": "LEXICAL",
               "MODIFIER": "LEXICAL", "ADJECTIVE": "LEXICAL"}


def jsonl(path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def pick(hyps, source, w, s2s_only=False):
    eligible = [j for j, h in enumerate(hyps) if source == "union" or h[source] is not None]
    if not eligible:
        raise ValueError(f"후보가 없는 출처: {source}")

    def score(j):
        h = hyps[j]
        return h["s2s"] if s2s_only else h["prior"] - h["ch"] + w * h["s2s"]
    return min(eligible, key=lambda j: (-score(j), hyps[j]["text"]))


def lift_ci(rows, ids, recs, before, after):
    """고정한 선택 결과에 대해 같은 정답문을 한 블록으로 한 paired bootstrap, 확인 부분만."""
    import numpy as np
    by_y = defaultdict(lambda: np.zeros(5, dtype=np.int64))
    for i in ids:
        a, b = (rows[i]["hyps"][j[i]] for j in (before, after))
        by_y[surface(recs[i]["y"])] += [len(a["tp"]), a["proposed"], len(b["tp"]), b["proposed"], rows[i]["ngold"]]
    groups = np.stack(list(by_y.values()))
    rng = np.random.default_rng(261125)
    samples = []
    for _ in range(2000):
        ca, pa, cb, pb, g = groups[rng.integers(len(groups), size=len(groups))].sum(axis=0)
        if not (g + 4 * pa and g + 4 * pb):
            raise ValueError("빈 bootstrap 분모")
        samples.append(5 * cb / (g + 4 * pb) - 5 * ca / (g + 4 * pa))
    return list(map(float, np.quantile(samples, [.025, .975])))


def single_edit_families(x, y, edits, annotation, matched):
    """M² TP의 구간 교체 전체를 *한* 정렬 영역으로 정확히 재현한 경우만 비교."""
    _, types, gold = annotation
    mapping = defaultdict(list)
    for e in edits:
        a, b = e["x"]
        c, d = e["y"]
        mapping[surface(x[:a] + y[c:d] + x[b:])].append(e["z"])
    xt = x.split()
    pairs = []
    for k in matched:
        a, b, _, corrs = gold[k]
        zz = [z for s in set(corrs) for z in mapping.get(" ".join(xt[:a] + s.split() + xt[b:]), [])]
        if len(zz) != 1 or types[k] not in GOLD_FAMILY:
            continue
        pred = zz[0]
        if pred.startswith(("PARTICLE_", "ENDING_")):
            pred = pred.partition("_")[0]
        pairs.append((GOLD_FAMILY[types[k]], pred))
    return pairs


def audit(rows, metrics, states, recs, ids, choices=None):
    hist, confusion = Counter(), Counter()
    n_hyps = n_changed = back = matched = 0
    for i in ids:
        hyps = rows[i]["hyps"]
        js = [choices[i]] if choices is not None else [j for j, h in enumerate(hyps) if h["s"] is not None]
        for j in js:
            h = hyps[j]
            n_hyps += 1
            n_changed += bool(h["edits"])
            for e in h["edits"]:
                hist[e["z"]] += 1
                back += e["backoff_only"]
            if choices is not None:
                tp = metrics[i]["hyps"][j]["tp"]
                matched += len(tp)
                confusion.update(single_edit_families(surface(recs[i]["x"]), h["text"], h["edits"], states[i], tp))
    n, ag = sum(hist.values()), sum(confusion.values())
    if not n:
        raise ValueError("검사할 변경 구간이 없다")
    out = {"hypotheses": n_hyps, "with_changes": n_changed, "changed_regions": n, "z": dict(hist),
           "backoff_only": back, "observed_route_rate": (n - back) / n,
           "unclassified_map": hist["UNCLASSIFIED"], "unclassified_map_rate": hist["UNCLASSIFIED"] / n}
    if choices is not None:
        out["auto_agreement"] = {"m2_tp": matched, "comparable_exact_region_tp": ag,
                                 "agreement": sum(v for (a, b), v in confusion.items() if a == b) / ag if ag else None,
                                 "confusion": [[a, b, n] for (a, b), n in sorted(confusion.items())]}
    return out


def blind_sample(out, rows, ids, choices, n=100):
    population = [(i, j) for i in ids for j in range(len(rows[i]["hyps"][choices[i]]["edits"]))]
    picks = random.Random(261125).sample(population, min(n, len(population)))

    def show(s):
        return json.dumps(s, ensure_ascii=False) if s else "∅"

    with (out / "review_blind.md").open("x", encoding="utf-8") as f, \
            (out / "review_key.jsonl").open("x", encoding="utf-8") as key:
        f.write("# C안 · KoBART 편집 독립 검수\n\n"
                "**x는 실제 입력, y는 KoBART가 제안한 출력(정답이 아님)**입니다. 문장 전체를 읽되 표시된 "
                "**x → y 한 구간만** 판정해 주세요. ∅는 빈 조각, 숫자는 문자 위치 [시작, 끝)입니다.\n\n"
                "① 교정 판정: `타당 / 둘 다 허용(문체·규범 선택) / 부적절 / 판단 어려움`. "
                "② 원문 오류의 주된 유형: `철자·타자 / 띄어쓰기 / 조사(대치·누락·과잉) / 어미(대치·누락·과잉) / "
                "어휘 / 통용 변이 / 문장부호 / 복합·기타 / 원문 오류 없음 / 판단 어려움`. "
                "모형의 설명·확률과 말뭉치 정답은 가렸습니다. 불확실하면 그대로 남겨주세요.\n\n")
        for idx, (i, j) in enumerate(picks, 1):
            row = rows[i]
            h = row["hyps"][choices[i]]
            e = h["edits"][j]
            x, y = surface(row["x"]), h["text"]
            a, b = e["x"]
            c, d = e["y"]
            code = f"C4-{idx:03}"
            f.write(f"## {code}\n\n- 원문 x: {show(x)}\n- 제안 y: {show(y)}\n"
                    f"- 검수 구간: x[{a}:{b}] {show(x[a:b])} → y[{c}:{d}] {show(y[c:d])}\n"
                    "- 교정 판정: \n- 원문 오류유형: \n- 메모: \n\n")
            key.write(json.dumps({"id": code, "i": i, "unit_id": row["unit_id"], "candidate": choices[i],
                                  "region": j, **e}, ensure_ascii=False) + "\n")
    return len(picks)


def main(out: Path):
    rows, metric, recs = (jsonl(p) for p in (out / "features.jsonl", out / "m2_stats.jsonl", DEV))
    blocks = m2_blocks(GOLD)
    b1 = FROZEN_B.read_text(encoding="utf-8").splitlines()
    archived = (Path("data/c_kobart/learner_s0_v1") / "val_hypothesis.txt").read_text(encoding="utf-8").splitlines()
    causal = {r["text"]: r["lp"] for r in jsonl(out / "causal.jsonl")}
    select, check, old_b, old_s = [], [], [], []
    for i, (r, m, record, b_text, s_text, block) in enumerate(zip(rows, metric, recs, b1, archived, blocks, strict=True)):
        if not (r["i"] == m["i"] == i and r["unit_id"] == record["unit_id"] and r["x"] == record["x"]
                and surface(r["x"]) == surface(block[0][2:])):
            raise ValueError(f"{i}: 입력 정렬 불일치")
        for h, hm in zip(r["hyps"], m["hyps"], strict=True):
            if any(h[k] != hm[k] for k in ("text", "h", "s")):
                raise ValueError(f"{i}: 평가 캐시 후보 불일치")
            h["prior"] = .5 * h["lm"] + causal[h["text"]]
            if not all(math.isfinite(h[k]) for k in ("prior", "ch", "s2s")):
                raise ValueError(f"{i}: 비유한 특성")
        old_b.append(next(j for j, h in enumerate(r["hyps"]) if h["h"] is not None and h["text"] == surface(b_text)))
        old_s.append(next(j for j, h in enumerate(r["hyps"]) if h["s"] == 0 and h["text"] == surface(s_text)))
        (select if is_selection("korean_learner", record) else check).append(i)
    if (len(select), len(check)) != (2131, 2133):
        raise ValueError("이 실험의 dev 분할 지문이 다르다")

    def rank(source, w, only=False):
        return [pick(r["hyps"], source, w, only) for r in rows]

    def sufficient(choices, ids):
        return aggregate([stats(metric[i], metric[i]["hyps"][choices[i]]) for i in ids])

    grid = {w: sufficient(rank("union", w), select).f for w in GRID}
    best = max(GRID, key=lambda w: (grid[w], -w))
    systems = {"kobart": old_s, "frozen_B": old_b, "union_s2s_only": rank("union", 0, True)}
    for source in ("h", "s", "union"):
        for tag, w in (("no_s2s", 0), ("selected", best)):
            systems[f"{source}_{tag}"] = rank(source, w)
    report = {"lambda4": best, "selection_grid": grid, "metrics": {}, "choice_origin": {},
              "reference_metric_verified": [], "check_delta_ci95": {}}
    partitions = {"select": select, "check": check, "all_dev": list(range(len(rows)))}
    # 모형별·분할별 캐시 합계를 별도 프로세스의 수정되지 않은 공식 CLI로 검증한다.
    with TemporaryDirectory(dir=out) as tmp:
        td = Path(tmp)
        for part, ids in partitions.items():
            goldfile = td / f"{part}.m2"
            write_m2([blocks[i] for i in ids], goldfile)
            report["metrics"][part] = {}
            for name, choices in systems.items():
                counts = sufficient(choices, ids).metrics()
                hp = td / "hyp.txt"
                hp.write_text("".join(rows[i]["hyps"][choices[i]]["text"] + "\n" for i in ids), encoding="utf-8")
                ref = m2score(hp, goldfile)
                if any(abs(v - counts[k]) > .000051 for k, v in ref.items()):
                    raise ValueError(f"{name}/{part}: 충분통계와 공식 CLI 불일치: {counts}, {ref}")
                report["metrics"][part][name] = counts
            report["reference_metric_verified"].append(part)
            origins = Counter()
            for i in ids:
                h = rows[i]["hyps"][systems["union_selected"][i]]
                origins["both" if h["s"] is not None and h["h"] is not None else
                        "s_only" if h["s"] is not None else "h_only"] += 1
            report["choice_origin"][part] = dict(origins)
    for name in ("kobart", "frozen_B", "s_selected", "union_no_s2s"):
        report["check_delta_ci95"][f"union_selected_minus_{name}"] = lift_ci(metric, check, recs, systems[name],
                                                                             systems["union_selected"])
    with (out / "choices.jsonl").open("x", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            f.write(json.dumps({"i": i, "unit_id": r["unit_id"], **{n: v[i] for n, v in systems.items()}}) + "\n")
    states = list(map(annotations, blocks))
    aud = {}
    for part in ("check", "all_dev"):
        ids = partitions[part]
        aud[part] = {"kobart_nbest": audit(rows, metric, states, recs, ids)}
        for name in ("kobart", "union_no_s2s", "union_selected"):
            aud[part][name] = audit(rows, metric, states, recs, ids, systems[name])
    aud["blind_check_kobart_edits"] = blind_sample(out, rows, check, old_s)
    (out / "audit.json").write_text(json.dumps(aud, ensure_ascii=False, indent=1))
    (out / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({"lambda4": best, "check": report["metrics"]["check"],
                      "delta_ci95": report["check_delta_ci95"], "blind": aud["blind_check_kobart_edits"]}), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    main(ap.parse_args().out)
