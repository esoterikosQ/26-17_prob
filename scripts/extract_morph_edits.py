"""A-3a 형태소 편집 추출: (x, y) 쌍 → A-0d 편집 구간 → 형태소 편집(m → m′)과 자동 z, KAGAS 유형 대조.

    python scripts/extract_morph_edits.py --sets korean_learner,native,lang8,ec --split train --ec-n 300000

- 구간: ``hnc.align.align_pair``(A-0d, 검수 통과). 구간의 y 형태소 = 정렬의 투영, x 형태소 = 오류문 x를 Kiwi로
  분석해 같은 구간에 걸친 형태소(m′의 근사, ``hnc.morph_channel`` 머리말).
- z: ``hnc.morph_channel``(부호 형태소를 떼고 형태소 정렬 → 조사·어미·어휘·표기·통용 변이·삭제·삽입).
  통용 변이 판정의 빈도표는 디코더 어휘(``datasets/vocab/eojeol_freq.tsv``: 교정문 + 신문 어절 빈도).
- 문맥 c: 형태소 품사 + 바로 앞 표층 문자의 받침(B = 받침 있음, V = 없음, O = 한글 아님/문장 처음) —
  조사·어미 이형태(을/를, 이/가, 아/어) 선택의 조건.
- Kor-*는 쌍마다 KAGAS 자동 유형(``kagas``: x 어절 위치)이 있어, 구간과 겹치는 KAGAS 편집의 유형과 대조한다.
- EC는 train에서 범주 구분 없이 ``--ec-n``쌍을 무작위로 뽑는다(동일 쌍도 포함해야 IDENT 기저율이 맞음).
- 출력: datasets/morph/<set>_<split>/{regions.jsonl, ctx_counts.json}, outputs/a3/extract_<set>_<split>.json(통계).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.align import Span, align_pair  # noqa: E402
from hnc.morph import MorphAnalyzer  # noqa: E402
from hnc.morph_channel import KAGAS_TO_Z, is_punct, morph_edits, region_z, variant_by_freq  # noqa: E402
from hnc.surface import split_punct  # noqa: E402

KOR = {"korean_learner", "native", "lang8"}


def load_freq(path: Path) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            w, _, n = line.rstrip("\n").rpartition("\t")
            out[w] = int(n)
    return out


def batchim(text: str, pos: int) -> str:
    if pos <= 0 or text[pos - 1].isspace():
        return "O"
    ch = text[pos - 1]
    if "가" <= ch <= "힣":
        return "B" if (ord(ch) - 0xAC00) % 28 else "V"
    return "O"


def eojeol_core(text: str, start: int, end: int) -> str:
    """[start, end)를 덮는 어절(들)의 핵심(앞뒤 부호 뗀 것)."""
    lo = text.rfind(" ", 0, start) + 1
    hi = text.find(" ", max(end, start))
    hi = len(text) if hi < 0 else hi
    return " ".join(split_punct(t)[1] for t in text[lo:hi].split())


def token_index(text: str):
    """문자 위치 → 공백 단위 어절 번호."""
    idx, k = [], 0
    for ch in text:
        idx.append(k)
        if ch == " ":
            k += 1
    return idx


def kagas_overlap(x: str, x_start: int, x_end: int, kagas) -> list:
    if not kagas:
        return []
    ti = token_index(x)
    if not ti:
        return []
    t0 = ti[min(x_start, len(ti) - 1)]
    t1 = ti[min(max(x_end - 1, x_start), len(ti) - 1)]
    out = []
    for s, e, typ, _ in kagas:
        if (s < e and s <= t1 and e > t0) or (s == e and t0 <= s <= t1 + 1):
            out.append(typ)
    return out


def load_records(name: str, split: str, ec_n: int, seed: int):
    if name in KOR:
        return [json.loads(line) for line in open(f"datasets/kor_gec/{name}_{split}.jsonl", encoding="utf-8")]
    rng = random.Random(seed)
    sample, seen = [], 0
    with open(f"datasets/ec/{split}.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["cat"] in ("empty", "placeholder"):
                continue
            seen += 1
            if len(sample) < ec_n:
                sample.append(r)
            else:
                k = rng.randint(0, seen - 1)
                if k < ec_n:
                    sample[k] = r
    return sample


def run(name: str, split: str, args, ana: MorphAnalyzer, freq: dict) -> dict:
    t0 = time.time()
    recs = [r for r in load_records(name, split, args.ec_n, args.seed) if r["x"].strip() and r["y"].strip()]
    out_dir = Path("datasets/morph") / f"{name}_{split}"
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = Counter()                    # (품사, 받침) → y 형태소 수 (부호 제외)
    zc = Counter()                     # 구간 대표 z
    ec_z = Counter()                   # 형태소 편집 z
    top = defaultdict(Counter)         # z → (m, m′) 빈도
    conf = defaultdict(Counter)        # KAGAS 유형(사상) → 구간 z
    kinds = Counter()
    n_regions = 0
    fq = lambda w: freq.get(w, 0)      # noqa: E731
    with open(out_dir / "regions.jsonl", "w", encoding="utf-8") as fo:
        for s in range(0, len(recs), args.chunk):
            chunk = recs[s:s + args.chunk]
            ysp = ana.spans_many([r["y"] for r in chunk])
            xsp = ana.spans_many([r["x"] for r in chunk])
            for r, ys, xs in zip(chunk, ysp, xsp):
                y, x = r["y"], r["x"]
                for f, t, a, b in ys:
                    if not is_punct((f, t)):
                        ctx[(t, batchim(y, a))] += 1
                if x == y:
                    continue
                for g in align_pair(y, x, [Span(*q) for q in ys]):
                    kinds[g.kind] += 1
                    if g.kind != "edit":
                        continue
                    n_regions += 1
                    yidx = [k for k, q in enumerate(ys) if q[2] < g.y_end and q[3] > g.y_start and not is_punct(q[:2])]
                    my = [ys[k][:2] for k in yidx]
                    mx = [q[:2] for q in xs if q[2] < g.x_end and q[3] > g.x_start] if g.x_seg.strip() else []
                    variant = variant_by_freq(fq, eojeol_core(y, g.y_start, g.y_end), eojeol_core(x, g.x_start, g.x_end))
                    edits = morph_edits(my, mx, variant)
                    # 각 편집의 문맥: m이 있으면 그 형태소, 삽입이면 왼쪽 y 형태소
                    p, last = 0, None
                    out_edits = []
                    for e in edits:
                        if e["m"] is not None:
                            k = yidx[p]
                            p += 1
                            last = k
                            c = (ys[k][1], batchim(y, ys[k][2]))
                        else:
                            c = ("INS_AFTER", ys[last][1] if last is not None else "BOS")
                        out_edits.append({"m": e["m"], "m2": e["m2"], "z": e["z"], "c": c})
                        ec_z[e["z"]] += 1
                        if e["z"] != "IDENT":
                            top[e["z"]][(e["m"] and "/".join(e["m"]), e["m2"] and "/".join(e["m2"]))] += 1
                    rz = region_z(out_edits)
                    zc[rz] += 1
                    kg = kagas_overlap(x, g.x_start, g.x_end, r.get("kagas"))
                    mapped = sorted({KAGAS_TO_Z.get(t, t) for t in kg})
                    if len(mapped) == 1:
                        conf[mapped[0]][rz] += 1
                    fo.write(json.dumps({"unit": r["unit_id"], "y_seg": g.y_seg, "x_seg": g.x_seg, "z": rz,
                                         "edits": out_edits, "kagas": kg}, ensure_ascii=False) + "\n")
    (out_dir / "ctx_counts.json").write_text(json.dumps([[t, b, n] for (t, b), n in ctx.most_common()],
                                                        ensure_ascii=False), encoding="utf-8")

    def agree(row):
        tot = sum(row.values())
        return {"n": tot, **{z: round(v / tot, 3) for z, v in row.most_common(4)}}
    stats = {"set": name, "split": split, "pairs": len(recs), "region_kinds": dict(kinds), "edit_regions": n_regions,
             "region_z": dict(zc.most_common()), "edit_z": dict(ec_z.most_common()),
             "kagas_vs_auto": {k: agree(v) for k, v in sorted(conf.items(), key=lambda kv: -sum(kv[1].values()))},
             "top": {z: [[a or "∅", b or "∅", n] for (a, b), n in c.most_common(args.top)] for z, c in top.items()},
             "seconds": round(time.time() - t0, 1)}
    Path("outputs/a3").mkdir(parents=True, exist_ok=True)
    Path(f"outputs/a3/extract_{name}_{split}.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1),
                                                              encoding="utf-8")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="korean_learner,native,lang8,ec")
    ap.add_argument("--split", default="train")
    ap.add_argument("--ec-n", type=int, default=300000)
    ap.add_argument("--freq", type=Path, default=Path("datasets/vocab/eojeol_freq.tsv"))
    ap.add_argument("--chunk", type=int, default=20000)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    t0 = time.time()
    freq = load_freq(args.freq)
    print(json.dumps({"freq_entries": len(freq), "load_sec": round(time.time() - t0, 1)}), flush=True)
    ana = MorphAnalyzer()
    for name in args.sets.split(","):
        st = run(name, args.split, args, ana, freq)
        print(json.dumps({k: st[k] for k in ("set", "pairs", "edit_regions", "region_z", "kagas_vs_auto", "seconds")},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
