"""B-1·B-3 신경 LM 채점: 디코더 n-best 가설의 표층 문장 점수(인과 LM 로그확률 / 마스크 LM PLL).

    .venv-nlm/bin/python scripts/nlm_score.py --tag native_em --model EleutherAI/polyglot-ko-1.3b --kind causal
    .venv-nlm/bin/python scripts/nlm_score.py --tag native_em --model klue/roberta-large --kind masked

- 입력: ``outputs/rescore/<tag>/nbest.jsonl`` (rescore.py가 만든 n-best). 출력: 같은 디렉토리의
  ``nlm_<name>.jsonl`` — 줄마다 ``{"i", "scores": [{"lp", "nt"}, ...]}`` (가설 순서 그대로).
- 토크나이저 불일치 처리(B-1): 신경 LM은 형태소가 아닌 **표층 문장 전체**를 자기 토크나이저로 채점한다.
  문장 단위 점수 log P(y)는 토큰화와 무관한 양이므로 형태소 LM·PCFG 점수와 그대로 가중합할 수 있다.
- causal: log P(y) = Σ_t log P(w_t | <bos>, w_<t) + log P(<eos> | ·) (자연로그).
- masked: PLL(y) = Σ_t log P(w_t | w_\\t) (Salazar et al. 2020; 토큰을 하나씩 가려 예측).
- ``nt`` = 채점한 토큰 수(길이 정규화용).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch


def load_texts(path: Path):
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    uniq = sorted({h["text"] for r in rows for h in r["hyps"]})
    return rows, uniq


@torch.no_grad()
def causal_scores(texts, model_name, batch_size, device, dtype):
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=dtype).to(device).eval()
    bos = tok.bos_token_id if tok.bos_token_id is not None else tok.eos_token_id
    eos = tok.eos_token_id
    pad = tok.pad_token_id if tok.pad_token_id is not None else eos
    enc = [[bos] + tok(t, add_special_tokens=False)["input_ids"] + [eos] for t in texts]
    order = sorted(range(len(texts)), key=lambda k: len(enc[k]))
    out = [None] * len(texts)
    for s in range(0, len(order), batch_size):
        idx = order[s:s + batch_size]
        L = max(len(enc[k]) for k in idx)
        ids = torch.full((len(idx), L), pad, dtype=torch.long)
        att = torch.zeros((len(idx), L), dtype=torch.long)
        for r, k in enumerate(idx):
            ids[r, :len(enc[k])] = torch.tensor(enc[k])
            att[r, :len(enc[k])] = 1
        ids, att = ids.to(device), att.to(device)
        logits = model(input_ids=ids, attention_mask=att).logits.float()
        lp = torch.log_softmax(logits[:, :-1], dim=-1).gather(-1, ids[:, 1:, None])[..., 0]
        lp = (lp * att[:, 1:]).sum(-1)
        for r, k in enumerate(idx):
            out[k] = {"lp": float(lp[r]), "nt": len(enc[k]) - 1}
    return out


@torch.no_grad()
def masked_scores(texts, model_name, batch_size, device, dtype):
    from transformers import AutoModelForMaskedLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForMaskedLM.from_pretrained(model_name, torch_dtype=dtype).to(device).eval()
    enc = [tok(t)["input_ids"] for t in texts]            # [CLS]/<s> … [SEP]/</s> 포함
    special = set(tok.all_special_ids)
    jobs = [(k, p) for k, ids in enumerate(enc) for p, t in enumerate(ids) if t not in special]
    jobs.sort(key=lambda j: len(enc[j[0]]))
    lp = [0.0] * len(texts)
    nt = [0] * len(texts)
    for s in range(0, len(jobs), batch_size):
        chunk = jobs[s:s + batch_size]
        L = max(len(enc[k]) for k, _ in chunk)
        ids = torch.full((len(chunk), L), tok.pad_token_id, dtype=torch.long)
        att = torch.zeros((len(chunk), L), dtype=torch.long)
        gold = torch.zeros(len(chunk), dtype=torch.long)
        pos = torch.zeros(len(chunk), dtype=torch.long)
        for r, (k, p) in enumerate(chunk):
            e = enc[k]
            ids[r, :len(e)] = torch.tensor(e)
            att[r, :len(e)] = 1
            gold[r], pos[r] = e[p], p
            ids[r, p] = tok.mask_token_id
        ids, att = ids.to(device), att.to(device)
        logits = model(input_ids=ids, attention_mask=att).logits
        rows = torch.arange(len(chunk), device=device)
        sel = torch.log_softmax(logits[rows, pos.to(device)].float(), dim=-1)
        g = sel[rows, gold.to(device)].tolist()
        for (k, _), v in zip(chunk, g):
            lp[k] += v
            nt[k] += 1
    return [{"lp": a, "nt": b} for a, b in zip(lp, nt)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--kind", choices=["causal", "masked"], required=True)
    ap.add_argument("--name", default=None, help="출력 이름(기본: 모델 이름 끝부분)")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--dtype", default="bfloat16")
    args = ap.parse_args()
    d = Path("outputs/rescore") / args.tag
    name = args.name or args.model.split("/")[-1]
    rows, uniq = load_texts(d / "nbest.jsonl")
    dtype = getattr(torch, args.dtype)
    t0 = time.time()
    fn = causal_scores if args.kind == "causal" else masked_scores
    scores = dict(zip(uniq, fn(uniq, args.model, args.batch_size, "cuda", dtype)))
    wall = time.time() - t0
    with open(d / f"nlm_{name}.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"i": r["i"], "scores": [scores[h["text"]] for h in r["hyps"]]}) + "\n")
    meta = {"model": args.model, "kind": args.kind, "name": name, "texts": len(uniq), "wall_sec": round(wall, 1),
            "gpu_mem_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)}
    (d / f"nlm_{name}.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
