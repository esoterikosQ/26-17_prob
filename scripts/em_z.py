"""A-3f z의 EM 재추정 — E단계: 학습 편집 구간마다 두 가설의 사후확률.

    python scripts/em_z.py --sets korean_learner --channel datasets/channel/learner_lang8_J \
        --morph-channel datasets/morph_channel/learner

- 자모 경로(SPELL): 형태소는 그대로(m′ = m), 비용 = Σ_m −log P(IDENT|m) − log P_J(x_구간 | y_구간).
- 형태소 경로: 규칙 z의 형태소 편집(표층 표기 판정이 SPELL로 돌린 구간은 그 전의 편집 ``edits_raw``),
  비용 = Σ 편집 비용(대치 −log P(m→m′), 누락 −log P(DEL|m,lt), 과잉 −log P(INS m′|lt), 동일 −log P(IDENT|m))
  − log P_J(x_구간 | x_구간)(x = R(m′) 가정). 표에 없는 편집은 ``--floor`` 비용.
- P(형태소 경로 | x, y) = σ(비용_자모 − 비용_형태소). 규칙 z와 사후 argmax z의 KAGAS 부류 일치율을 비교한다.
- ``--write``: 구간마다 사후확률 ``p_morph``를 붙인 regions_em.jsonl(M단계 입력)을 쓴다.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.jamo_channel import ChannelParams, JamoChannel  # noqa: E402
from hnc.morph_channel import KAGAS_TO_Z, MorphChannel, _key, region_family  # noqa: E402

_J = None
_M = None
_SUB = None
_DEL = None
_FLOOR = 12.0


def edit_cost(e) -> float:
    m, m2 = e["m"], e["m2"]
    if m is not None and m2 is not None:
        if m == m2:
            return _M.ident_cost(tuple(m))
        return _SUB.get((_key(m2), _key(m)), _FLOOR)
    if m is not None:
        return _DEL.get((e["lt"], _key(m)), _FLOOR)
    cn = _M.ins.get(f"{_key(m2)}|{e['lt']}")
    return cn[0] if cn else _FLOOR


def _work(r):
    edits = r["edits"]
    hyp = r.get("edits_raw") or (edits if r["z"] != "SPELL" else None)
    my = [e["m"] for e in edits if e["m"] is not None]
    y, x = r["y_seg"], r["x_seg"]
    c_spell = sum(_M.ident_cost(tuple(m)) for m in my) + _J.neglogprob(y, x)
    if hyp is None:
        return r, None, c_spell, None
    c_morph = sum(edit_cost(e) for e in hyp) + _J.neglogprob(x, x)
    d = c_spell - c_morph
    p = 1.0 / (1.0 + math.exp(-d)) if d > -50 else 0.0
    return r, p, c_spell, c_morph


def main() -> None:
    global _J, _M, _SUB, _DEL, _FLOOR
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="korean_learner")
    ap.add_argument("--channel", type=Path, default=Path("datasets/channel/learner_lang8_J"))
    ap.add_argument("--morph-channel", type=Path, default=Path("datasets/morph_channel/learner"))
    ap.add_argument("--floor", type=float, default=12.0)
    ap.add_argument("--max-regions", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    _FLOOR = args.floor
    params = ChannelParams.from_json((args.channel / "params.json").read_text(encoding="utf-8"))
    others = json.loads((args.channel / "other_symbols.json").read_text(encoding="utf-8"))
    _J = JamoChannel(params, others)
    _M = MorphChannel.from_json((args.morph_channel / "params.json").read_text(encoding="utf-8"))
    _SUB = {(k, _key(m)): c for k, v in _M.sub.items() for m, _z, c, _n in v}
    _DEL = {(lt, _key(m)): c for lt, v in _M.dele.items() for m, c, _n in v}
    for name in args.sets.split(","):
        path = Path("datasets/morph") / f"{name}_train" / "regions.jsonl"
        regs = [json.loads(line) for line in open(path, encoding="utf-8")]
        if args.max_regions:
            regs = regs[:args.max_regions]
        with mp.get_context("fork").Pool(args.workers) as pool:
            res = list(pool.imap(_work, regs, chunksize=64))
        hist = defaultdict(Counter)
        agree_rule = agree_em = n_cmp = 0
        flips = Counter()
        fo = open(path.with_name("regions_em.jsonl"), "w", encoding="utf-8") if args.write else None
        for r, p, cs, cm in res:
            rule_fam = r["family"]
            if p is None:
                em_fam = rule_fam
            else:
                hist[r["z"]][min(int(p * 10), 9)] += 1
                hyp = r.get("edits_raw") or r["edits"]
                em_fam = region_family(hyp) if p > 0.5 else "SPELL"
                if em_fam != rule_fam:
                    flips[(rule_fam, em_fam)] += 1
            kg = sorted({KAGAS_TO_Z.get(t, t) for t in r.get("kagas", [])})
            if len(kg) == 1 and kg[0] in ("PARTICLE", "ENDING", "LEXICAL", "SPELL", "DEL", "INS"):
                n_cmp += 1
                agree_rule += rule_fam == kg[0]
                agree_em += em_fam == kg[0]
            if fo:
                r2 = dict(r)
                r2["p_morph"] = p
                fo.write(json.dumps(r2, ensure_ascii=False) + "\n")
        if fo:
            fo.close()
        print(json.dumps({"set": name, "regions": len(res),
                          "posterior_hist_by_rule_z(p_morph 0.0~1.0 10칸)": {z: [h[k] for k in range(10)] for z, h in hist.items()},
                          "kagas_agreement": {"n": n_cmp, "rule": round(agree_rule / n_cmp, 4) if n_cmp else None,
                                              "em_posterior": round(agree_em / n_cmp, 4) if n_cmp else None},
                          "flips(rule→em)": [[a, b, n] for (a, b), n in flips.most_common(10)]}, ensure_ascii=False),
              flush=True)


if __name__ == "__main__":
    main()
