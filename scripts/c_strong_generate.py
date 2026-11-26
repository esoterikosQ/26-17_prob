"""Frozen gec2 KoBART beams and conditional scores (dev only; no Lightning training)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import torch
from transformers import AutoTokenizer, BartForConditionalGeneration

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface, union_pool
from scripts.c_kobart import seq_logprob
from scripts.c_transfer_pool import candidates, rows

ROOT = Path("outputs/c_gec2_dev_v1")
ASSETS = Path("data/c_gec2_dev_v1/models")


def verified_model(domain, recipe):
    src = ASSETS / domain
    files = {}
    for line in (ROOT / "restore" / f"{domain}.sha256").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        if Path(name).name != name:
            raise ValueError("invalid checksum manifest")
        with (src / name).open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != expected:
                raise ValueError(f"{domain}: checksum mismatch for {name}")
        files[name] = expected
    required = {"config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
                "model.safetensors", "best_info.json"}
    if not required <= files.keys():
        raise ValueError("incomplete verified checkpoint")
    best = json.loads((src / "best_info.json").read_text())
    if best["epoch"] != recipe["epoch"] or abs(best["val_gleu"] - recipe["val_gleu"]) > .00011:
        raise ValueError("checkpoint is not the historical best dev epoch")
    tok = AutoTokenizer.from_pretrained(src, local_files_only=True, trust_remote_code=False)
    model = BartForConditionalGeneration.from_pretrained(src, local_files_only=True, use_safetensors=True)
    if ((tok.bos_token_id, tok.eos_token_id, tok.pad_token_id, model.config.decoder_start_token_id) != (0, 1, 3, 1)
            or model.generation_config.decoder_start_token_id != 1 or model.generation_config.forced_eos_token_id != 1):
        raise ValueError("gec2 BART IDs differ from the verified recipe")
    return tok, model.eval().to("cuda"), files


def encode(tok, texts, wrap, *, target=False):
    values = (tok(text_target=texts) if target else tok(texts))["input_ids"]
    ids = [[tok.bos_token_id, *a, tok.eos_token_id] if wrap else a for a in values]
    if any(len(a) < 2 or a[0] != tok.bos_token_id or a[-1] != tok.eos_token_id for a in ids):
        raise ValueError("gec2 requires BOS and EOS on source and target")
    return ids


def padded(ids, pad):
    width = max(map(len, ids))
    return torch.tensor([a + [pad] * (width - len(a)) for a in ids], device="cuda")


def inputs(ids):
    return {"input_ids": padded(ids, 3), "attention_mask": padded([[1] * len(a) for a in ids], 0)}


@torch.inference_mode()
def score_batch(model, xs, ys):
    labels = padded(ys, -100)
    shifted = model.prepare_decoder_input_ids_from_labels(labels=labels)
    logits = model(**inputs(xs), decoder_input_ids=shifted, use_cache=False).logits
    scores = seq_logprob(logits, labels).tolist()
    if not all(map(math.isfinite, scores)):
        raise ValueError("non-finite teacher-forced score")
    return scores


@torch.inference_mode()
def prefix_score(model, x, y):
    enc = inputs([x])
    prefix = [model.config.decoder_start_token_id]
    summed = 0.
    for t in y:
        logits = model(**enc, decoder_input_ids=padded([prefix], 3), use_cache=False).logits[0, -1].float()
        summed += logits.log_softmax(-1)[t].item()
        prefix.append(t)
    return summed


def run(domain, smoke):
    data = ROOT / domain
    out = data / "smoke" if smoke else data
    out.mkdir(exist_ok=True)
    recipe = json.loads((data / "recipe.json").read_text())
    recs = rows(data / "records.jsonl")
    if len(recs) != recipe["n"] or recipe["domain"] != domain:
        raise ValueError("input fingerprint mismatch")
    if smoke:
        recs = recs[:64]
    generate_pool(domain, out, recs, candidates(recs), recipe, archived=[r["archived"] for r in recs])


@torch.inference_mode()
def generate_pool(domain, out, recs, hs, recipe, *, archived=None):
    """With no archived baseline, S[0] is the newly generated beam-4 decision."""
    if not recs or len(recs) != len(hs) or (archived is not None and len(archived) != len(recs)):
        raise ValueError("incomplete source/beam inputs")
    tok, model, hashes = verified_model(domain, recipe)
    wrap = tok("가")["input_ids"][-1] != tok.eos_token_id  # original gec2 Formatter
    xs = encode(tok, [r["x"] for r in recs], wrap)
    if max(map(len, xs)) > model.config.max_position_embeddings:
        raise ValueError("source exceeds model context; do not truncate this dev run")
    # Same 64-sentence length-sorted batches and generation cap as the archived beam 4.
    order = sorted(range(len(recs)), key=lambda i: -len(xs[i]))
    pools = [None] * len(recs)
    match = 0
    for k in range(0, len(order), 64):
        group = order[k:k + 64]
        bound = min(512, 2 * max(len(xs[i]) for i in group) + 10)
        opts = dict(do_sample=False, max_new_tokens=bound, early_stopping=True, use_cache=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            four = model.generate(**inputs([xs[i] for i in group]), num_beams=4, **opts)
            first = tok.batch_decode(four[:, 1:], skip_special_tokens=True)
            top = {i: surface(text) for i, text in zip(group, first, strict=True)}
            if archived is not None:
                match += sum(top[i] == archived[i] for i in group)
            for start in range(0, len(group), 16):
                ids = group[start:start + 16]
                eight = model.generate(**inputs([xs[i] for i in ids]), num_beams=8, num_return_sequences=8, **opts)
                texts = tok.batch_decode(eight[:, 1:], skip_special_tokens=True)
                for j, i in enumerate(ids):
                    baseline = top[i] if archived is None else archived[i]
                    seq = list(dict.fromkeys([baseline, *map(surface, texts[8*j:8*j+8])]))[:8]
                    pools[i] = union_pool(hs[i], seq)
        if k % 1024 == 0:
            print(f"{domain} beam rows {min(k+64, len(order))}/{len(order)}", flush=True)
    rate = match / len(recs)
    if archived is not None and rate < .95:
        raise ValueError(f"{domain}: regenerated beam-4 identity {match}/{len(recs)} below .95")
    probe = encode(tok, ["가 나"], wrap, target=True)[0]
    error = abs(prefix_score(model, xs[0], probe) - score_batch(model, [xs[0]], [probe])[0])
    if error > .001:
        raise ValueError(f"teacher forcing disagrees with token-by-token accumulation: {error}")
    fn = out / "pool.jsonl"
    partial = fn.with_suffix(".partial")
    if fn.exists():
        raise FileExistsError(fn)
    n_h = n_s = n_u = 0
    with partial.open("x", encoding="utf-8") as f:
        for start in range(0, len(recs), 16):
            chunk = [(i, h) for i in range(start, min(start+16, len(recs))) for h in pools[i]]
            ys = encode(tok, [h["text"] for _, h in chunk], wrap, target=True)
            if max(map(len, ys)) > model.config.max_position_embeddings:
                raise ValueError("candidate exceeds decoder context")
            for j in range(0, len(chunk), 16):
                part = chunk[j:j+16]
                for (_, h), v in zip(part, score_batch(model, [xs[i] for i, _ in part], ys[j:j+16]), strict=True):
                    h["s2s"] = v
            for i in range(start, min(start + 16, len(recs))):
                hyps = pools[i]
                n_h += sum(h["h"] is not None for h in hyps)
                n_s += sum(h["s"] is not None for h in hyps)
                n_u += len(hyps)
                r = recs[i]
                f.write(json.dumps({"i": i, "unit_id": r["unit_id"], "x": r["x"], "hyps": hyps},
                                   ensure_ascii=False) + "\n")
    partial.rename(fn)
    meta = {**recipe, "n": len(recs), "n_h": n_h, "n_s": n_s, "n_union": n_u,
            "beam4_match": match if archived is not None else None,
            "baseline_origin": "archived" if archived is not None else "fresh_beam4",
            "prefix_check_abs_error": error, "model_sha256": hashes["model.safetensors"], "wrap": wrap,
            "torch": torch.__version__, "transformers": __import__("transformers").__version__}
    with (out / "generation.json").open("x") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps(meta), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", choices=("korean_learner", "lang8", "union"))
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    run(args.domain, args.smoke)
