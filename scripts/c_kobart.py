"""C-0/C-1. 검증된 로컬 KoBART를 불러와 dev 후보 생성 및 합집합 전체 teacher forcing."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import torch
from transformers import BartConfig, BartForConditionalGeneration, PreTrainedTokenizerFast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface, union_pool  # noqa: E402

CKPT_SHA = "8b42ea50e717b538e4fb6d24c84e37f586cf57562159db7eefe0fada04b7ba26"
DEV = Path("datasets/kor_gec/korean_learner_dev.jsonl")
HNC = Path("outputs/rescore/learner_JJ_Ml30")
ASSETS = Path("data/c_kobart/learner_s0_v1")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_model(assets: Path):
    ckpt = assets / "learner.ckpt"
    if digest(ckpt) != CKPT_SHA:
        raise ValueError("체크포인트가 고정된 Learner seed 0 가중치와 다르다")
    tok = PreTrainedTokenizerFast(tokenizer_file=str(assets / "tokenizer.json"),
                                  **json.loads((assets / "special_tokens_map.json").read_text()))
    if (tok.bos_token_id, tok.eos_token_id, tok.pad_token_id, tok.vocab_size) != (0, 1, 3, 30000):
        raise ValueError("레거시 토크나이저 특수 기호 불일치")
    cfg = BartConfig.from_pretrained(assets, local_files_only=True)
    model = BartForConditionalGeneration(cfg)
    ck = torch.load(ckpt, map_location="cpu", weights_only=True, mmap=True)
    if ck["epoch"] != 3 or not all(k.startswith("model.") for k in ck["state_dict"]):
        raise ValueError("epoch 또는 체크포인트 키 불일치")
    model.load_state_dict({k.removeprefix("model."): v for k, v in ck["state_dict"].items()}, strict=True)
    return tok, model.eval().to("cuda")


def source_batch(tok, texts):
    ids = tok(texts, add_special_tokens=False)["input_ids"]
    if any(len(a) > 128 for a in ids):
        raise ValueError("이 dev 실행은 입력을 잘라내지 않는다")
    # 기존 dataset.py는 0으로 128칸까지 패딩하고 generate()가 config의 pad=3으로 마스크했다.
    src = torch.tensor([a + [0] * (128 - len(a)) for a in ids], device="cuda")
    return {"input_ids": src, "attention_mask": src.ne(3).long()}


def seq_logprob(logits, labels):
    selected = torch.log_softmax(logits.float(), dim=-1).gather(-1, labels.clamp_min(0).unsqueeze(-1)).squeeze(-1)
    return selected.masked_fill(labels == -100, 0.0).sum(dim=1)


@torch.inference_mode()
def conditional(tok, model, pairs, batch_size):
    scores = []
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        ys = [a + [1] for a in tok([y for _, y in batch], add_special_tokens=False)["input_ids"]]
        width = max(map(len, ys))
        if width > model.config.max_position_embeddings:
            raise ValueError("교정문이 KoBART 문맥 길이를 넘음")
        labels = torch.tensor([a + [-100] * (width - len(a)) for a in ys], device="cuda")
        shifted = torch.tensor([[0] + a[:-1] + [3] * (width - len(a)) for a in ys], device="cuda")
        logits = model(**source_batch(tok, [x for x, _ in batch]), decoder_input_ids=shifted,
                       use_cache=False).logits
        scores.extend(seq_logprob(logits, labels).tolist())
    if not all(math.isfinite(v) for v in scores):
        raise ValueError("KoBART 조건부 점수가 유한하지 않다")
    return scores


@torch.inference_mode()
def generate(args):
    assets = ASSETS
    records = [json.loads(l) for l in DEV.read_text(encoding="utf-8").splitlines()]
    hnc = [json.loads(l) for l in (HNC / "nbest.jsonl").read_text(encoding="utf-8").splitlines()]
    hsrc = [s[2:] for s in (HNC / "gold_all.m2").read_text(encoding="utf-8").splitlines() if s.startswith("S ")]
    refs = [(assets / f"val_{name}.txt").read_text(encoding="utf-8").splitlines()
            for name in ("source", "reference", "hypothesis")]
    if not len(records) == len(hnc) == len(hsrc) == 4264 or any(len(a) != len(records) for a in refs):
        raise ValueError("공식 dev 문장 수 불일치")
    for i, (r, h) in enumerate(zip(records, hnc, strict=True)):
        if (h["i"] != i or not h["hyps"] or surface(refs[0][i]) != surface(r["x"]) or
                surface(hsrc[i]) != surface(r["x"]) or surface(refs[1][i]) != surface(r["y"])):
            raise ValueError(f"{i}: 후보원과 dev 식별자/원문/참조 불일치")
    if args.limit:
        records = records[:args.limit]
    tok, model = load_model(assets)
    opts = dict(max_length=128, decoder_start_token_id=0, eos_token_id=1, forced_eos_token_id=None,
                forced_bos_token_id=None, repetition_penalty=1.0, do_sample=False)
    match, n_s, n_h, n_u, empty = 0, 0, 0, 0, 0
    with (args.out / "pool.jsonl").open("x", encoding="utf-8") as out:
        for start in range(0, len(records), args.batch):
            rs = records[start:start + args.batch]
            inputs = source_batch(tok, [r["x"] for r in rs])
            four = tok.batch_decode(model.generate(**inputs, **opts, num_beams=4), skip_special_tokens=True)
            eight = tok.batch_decode(model.generate(**inputs, **opts, num_beams=8, num_return_sequences=8),
                                     skip_special_tokens=True)
            rows, pairs = [], []
            for j, r in enumerate(rs):
                i = start + j
                archived = surface(refs[2][i])
                match += surface(four[j]) == archived
                seq = list(dict.fromkeys([archived, *map(surface, eight[8*j:8*j+8])]))[:8]
                hyps = union_pool([h["text"] for h in hnc[i]["hyps"]], seq)
                n_s += len(seq)
                n_h += sum(h["h"] is not None for h in hyps)
                n_u += len(hyps)
                empty += sum(not h["text"] for h in hyps)
                rows.append({"i": i, "unit_id": r["unit_id"], "x": r["x"], "hyps": hyps})
                pairs.extend((r["x"], h["text"]) for h in hyps)
            scores = iter(conditional(tok, model, pairs, args.batch * 2))
            for row in rows:
                for h in row["hyps"]:
                    h["s2s"] = next(scores)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    meta = {"checkpoint_sha256": CKPT_SHA, "dev_sha256": digest(DEV), "hnc_sha256": digest(HNC / "nbest.jsonl"),
            "tokenizer_sha256": digest(assets / "tokenizer.json"), "n": len(records), "beam4_match": match,
            "s_hyps": n_s, "h_hyps": n_h, "union_hyps": n_u, "empty_hyps": empty, "options": opts}
    (args.out / "generation.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.batch < 1 or not 0 <= args.limit <= 4264:
        ap.error("잘못된 batch 또는 limit")
    args.out.mkdir(parents=True, exist_ok=True)
    generate(args)
