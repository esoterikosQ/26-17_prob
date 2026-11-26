"""Aggregate an explicitly completed legacy C4 review under evaluation/final_v1.md."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

TEMPLATE_SHA = "25cb2be47c7bdc19e21db87b2c603d1f8795cada7581adbe7058a05beaf6a4ac"
KEY_SHA = "f035644c2eaef50173d9928fbcdd84c5b1ec56942dafc31e11ffcec9664453bf"
IDS = {f"C4-{i:03d}" for i in range(1, 101)}
ANCHORS = ("원문 x", "제안 y", "검수 구간")
FIELDS = (*ANCHORS, "교정 판정", "원문 오류유형")
VALIDITY = ("타당", "둘 다 허용", "부적절", "판단 어려움", "미응답")
HUMAN = {"철자·타자": "JAMO", "띄어쓰기": "WS", "조사": "PARTICLE", "어미": "ENDING",
         "어휘": "LEXICAL", "통용 변이": "VARIANT", "문장부호": "PUNCT", "복합·기타": "MIXED",
         "원문 오류 없음": "NO_ERROR", "판단 어려움": "UNCERTAIN", "": "MISSING"}
SPECIFIC = set(HUMAN.values()) - {"NO_ERROR", "UNCERTAIN", "MISSING"}
MODEL = {z: z for z in ("JAMO", "WS", "PARTICLE", "ENDING", "LEXICAL", "VARIANT", "MIXED", "UNCLASSIFIED")}
for op in ("INS", "DEL"):
    MODEL.update({f"PARTICLE_{op}": "PARTICLE", f"ENDING_{op}": "ENDING", f"MORPH_{op}": "UNSPECIFIED_MORPH"})


def parse_sheet(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    item = None
    in_note = False
    for line in text.splitlines():
        if line.startswith("## "):
            code = line[3:].strip()
            if not re.fullmatch(r"C4-\d{3}", code) or code in result:
                raise ValueError("invalid or duplicate review ID")
            item = result[code] = {}
            in_note = False
        elif item is not None and line.strip():
            field, sep, value = line.removeprefix("- ").partition(":")
            if line.startswith("- ") and sep and field in FIELDS:
                if field in item:
                    raise ValueError(f"duplicate field: {field}")
                item[field] = value.strip()
                in_note = False
            elif line.startswith("- 메모:"):
                in_note = True
            elif not in_note:
                raise ValueError("unexpected review line; enter judgments in their fields")
    if any(set(r) != set(FIELDS) for r in result.values()):
        raise ValueError("incomplete review structure; retain empty fields")
    return result


def judgments(template, form, expected):
    if set(template) != expected or set(form) != expected:
        raise ValueError("extra or missing review IDs")
    out = {}
    for code, row in form.items():
        if any(row[k] != template[code][k] for k in ANCHORS):
            raise ValueError(f"{code}: original source, proposal or region changed")
        val = row["교정 판정"] or "미응답"
        if val == "둘 다 허용(문체·규범 선택)":
            val = "둘 다 허용"
        typ = row["원문 오류유형"]
        sub = re.fullmatch(r"(조사|어미)\s*\((대치·누락·과잉|대치|누락|과잉)\)", typ)
        if sub:
            typ = sub[1]
        if val not in VALIDITY or typ not in HUMAN:
            raise ValueError(f"{code}: unrecognized judgment; clarify while blinded")
        out[code] = val, HUMAN[typ]
    if not any(v != "미응답" or t != "MISSING" for v, t in out.values()):
        raise ValueError("no human judgments; do not open the key")
    return out


def fraction(correct, n):
    return {"count": correct, "denominator": n, "rate": correct / n if n else None}


def summarize(human, keys):
    ref = {r["id"]: r for r in keys}
    if not human or len(ref) != len(keys) or ref.keys() != human.keys():
        raise ValueError("duplicate, missing or extra key IDs")
    valid, types, matrix = Counter(), Counter(), Counter()
    # The saved fine-grained MAP is the decision; confidence pools its family's mass.
    cal = [[] for _ in range(3)]
    n = agree = vn = va = typed = back = unclassified = 0
    for code, (v, t) in human.items():
        row = ref[code]
        z, post, bo = row["z"], row["posterior"], row["backoff_only"]
        if (type(bo) is not bool or z not in MODEL or z not in post or not set(post) <= MODEL.keys()
                or any(not math.isfinite(p) or not 0 <= p <= 1 for p in post.values())):
            raise ValueError(f"{code}: invalid frozen prediction")
        mass = sum(post.values())
        if abs(mass - 1) > (len(post) + 1)*.0000005 or post[z] < max(post.values()) - .000001:
            raise ValueError(f"{code}: invalid six-decimal posterior or inconsistent MAP")
        predicted = MODEL[z]
        valid[v] += 1
        types[t] += 1
        matrix[t, predicted] += 1
        back += bo
        unclassified += z == "UNCLASSIFIED"
        if t not in SPECIFIC:
            continue
        hit = t == predicted
        n += 1
        agree += hit
        if v == "타당":
            vn += 1
            va += hit
        if predicted in SPECIFIC:
            typed += 1
            confidence = sum(p for k, p in post.items() if MODEL[k] == predicted) / mass
            cal[0 if confidence < .5 else 1 if confidence < .8 else 2].append((confidence, hit))
    occupied = [len(b) for b in cal if b]
    ready = typed >= 50 and len(occupied) >= 2 and min(occupied) >= 10
    bins, gap = [], 0.
    for label, b in zip(("[0,.5)", "[.5,.8)", "[.8,1]"), cal, strict=True):
        conf = sum(c for c, _ in b) / len(b) if b else None
        acc = sum(h for _, h in b) / len(b) if b else None
        bins.append({"bin": label, "n": len(b), "confidence": conf, "agreement": acc})
        if conf is not None and acc is not None:
            gap += len(b)*abs(acc-conf)
    definite = sum(valid[k] for k in VALIDITY[:3])
    return {"protocol": "final_v1", "regions": len(human),
            "validity": {k: fraction(valid[k], len(human)) for k in VALIDITY},
            "valid_strict": fraction(valid["타당"], definite),
            "acceptable_including_variation": fraction(valid["타당"]+valid["둘 다 허용"], definite),
            "human_types": {k: types[k] for k in sorted(set(HUMAN.values()))},
            "confusion_human_model": [[a, b, c] for (a, b), c in sorted(matrix.items())],
            "type_agreement": fraction(agree, n), "type_agreement_valid_only": fraction(va, vn),
            "typed_coverage": fraction(typed, n), "map_unclassified": fraction(unclassified, len(human)),
            "backoff_only": fraction(back, len(human)),
            "calibration": {"n": typed, "bins": bins, "ece": gap / typed if ready else None,
                            "status": "descriptive" if ready else "insufficient_sample_or_bin_support"}}


def main(form: Path, template: Path, key: Path, out: Path, *, review_complete: bool):
    if not review_complete:
        raise ValueError("reviewer must declare completion before unblinding")
    if out.exists():
        raise FileExistsError(out)
    blank, completed = template.read_bytes(), form.read_bytes()
    if hashlib.sha256(blank).hexdigest() != TEMPLATE_SHA:
        raise ValueError("wrong blank template")
    human = judgments(parse_sheet(blank.decode("utf-8")), parse_sheet(completed.decode("utf-8")), IDS)
    raw = key.read_bytes()  # open only after checking every blind response/anchor
    if hashlib.sha256(raw).hexdigest() != KEY_SHA:
        raise ValueError("wrong archived key")
    result = summarize(human, [json.loads(line) for line in raw.splitlines()])
    result["sha256"] = {"judgments": hashlib.sha256(completed).hexdigest(), "template": TEMPLATE_SHA, "key": KEY_SHA}
    with out.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, allow_nan=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", type=Path, required=True)
    ap.add_argument("--template", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--review-complete", action="store_true", required=True)
    args = ap.parse_args()
    main(**vars(args))
