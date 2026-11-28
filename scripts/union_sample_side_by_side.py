"""Export an offline, private comparison of the exact archived corrections on the fixed Union 1k sample."""

from __future__ import annotations

import os
import sys
from collections import Counter
from difflib import SequenceMatcher
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface
from scripts.domain_report import SEALS
from scripts.final_test import INPUT, N, original, read
from scripts.haiku_union_sample import IDS_SHA, SOURCE_SHA, sample
from scripts.rehearsal_predict import C_NAMES, line, rows, save, sha

DATA = Path("data/union_1k_side_by_side_v1")
OUT = Path("outputs/union_1k_side_by_side_v1")
HAIKU_SEAL = "3ffca7f6f6c0d6e4b44c8db3291d388666b2774f5a599f1e36989dabdbcad69e"
PAIR_SHA = "65b1bd0039ae58c136ee44ebbb6876ee69a5353bb8c00046ec235e7e79592f28"
MAIN = {"haiku": "Haiku 5.5 Low", "kobart/r9": "KoBART", "kobart/r11": "KoBART + HNC",
        "t5/r9": "T5-large", "t5/r11": "T5-large + HNC"}
EXTRA = {"kobart/s_only": "KoBART · S8만", "kobart/r10": "KoBART · 합집합 λ=0",
         "t5/s_only": "T5-large · S8만", "t5/r10": "T5-large · 합집합 λ=0"}
DOMAINS = {"lang8": "Lang8", "korean_learner": "Learner", "native": "Native"}


def haiku_texts(ids, stream):
    results = []
    for i, row in zip(ids, stream, strict=True):
        if row["unit_id"] != f"union:test:{i}" or not isinstance(row["prediction"], str):
            raise ValueError(f"{i}: missing, reordered or invalid Haiku prediction")
        results.append(row["prediction"])
    return results


def comparisons():
    ids, id_text = sample()
    src_file, pairs = original("union"), INPUT/"gold/union_test.txt"
    if sha(src_file) != SOURCE_SHA or sha(pairs) != PAIR_SHA:
        raise ValueError("official Union corpus changed")
    src = src_file.read_text(encoding="utf-8").splitlines()
    refs = []
    # The distributed pair file has an empty separator; a line with a tab is a real pair.
    for x, pair in zip(src, (l for l in pairs.read_text(encoding="utf-8").splitlines() if l != ""), strict=True):
        a, b = pair.split("\t")
        if a != x:
            raise ValueError("paired file and original source disagree")
        refs.append(b)
    if len(src) != N["union"]:
        raise ValueError("incomplete Union source")
    haiku = DATA/"haiku"
    proof = read(haiku/"seal.json")
    hp = haiku/"predictions.jsonl"
    if (sha(haiku/"seal.json") != HAIKU_SEAL or proof["jsonl_sha"] != sha(hp)
            or proof["ids_sha"] != IDS_SHA or proof["n"] != len(ids)
            or sha(haiku/"sample_ids.txt") != IDS_SHA or (haiku/"sample_ids.txt").read_text() != id_text):
        raise ValueError("Haiku seal or sample mismatch")
    texts = haiku_texts(ids, rows(hp))
    records = {}
    for k, (i, h) in enumerate(zip(ids, texts, strict=True), 1):
        component = ("lang8" if i < N["lang8"] else
                     "korean_learner" if i < N["lang8"] + N["korean_learner"] else "native")
        records[i] = {"sample_no": k, "unit_id": f"union:test:{i}", "component": component,
                      "source": src[i], "reference": refs[i], "haiku": h}
    for family, name in (("kobart", "union/predictions.full.jsonl"), ("t5", "test/union/predictions.jsonl")):
        root = DATA/family
        if sha(root/"seal.json") != SEALS[family] or sha(root/name) != read(root/"seal.json")["sha256"][name]:
            raise ValueError(f"{family}: wrong sealed predictions")
        for i, (x, row) in enumerate(zip(src, rows(root/name), strict=True)):
            if (row["i"] != i or row["unit_id"] != f"union:test:{i}" or row["x"] != x
                    or set(row["predictions"]) != set(C_NAMES)):
                raise ValueError(f"{family}/{i}: source, occurrence or conditions changed")
            if i in records:
                for which, y in row["predictions"].items():
                    if not isinstance(y, str):
                        raise ValueError(f"{family}/{i}/{which}: missing text")
                    records[i][f"{family}/{which}"] = y
    result = list(records.values())
    old = read(haiku/"summary.json")
    if (old["seal"] != HAIKU_SEAL or old["sample"]["source_counts"] != dict(Counter(r["component"] for r in result))
            or sum(surface(r["haiku"]) != surface(r["source"]) for r in result) != old["changed"]):
        raise ValueError("export disagrees with scored sample")
    return result


def diff_html(source: str, target: str) -> str:
    def mark(tag: str, text: str) -> str:
        body = escape(text).replace(" ", '<span class="space"> </span>')
        return f"<{tag}>{body}</{tag}>"

    fragments = []
    for op, i, j, k, l in SequenceMatcher(None, source, target, autojunk=False).get_opcodes():
        if op == "equal":
            fragments.append(escape(target[k:l]))
        else:
            if i != j:
                fragments.append(mark("del", source[i:j]))
            if k != l:
                fragments.append(mark("ins", target[k:l]))
    return "".join(fragments)


STYLE = """
*{box-sizing:border-box} body{margin:1rem;font:16px/1.5 system-ui,sans-serif;color:#142536;background:#fff}
h1{font-size:1.55rem;margin:0} p,nav{margin:.4rem 0} label{margin-right:1rem;cursor:pointer}
a{color:#18548c} .scroll{overflow:auto;max-height:73vh;margin-top:.7rem;border:1px solid #aeb9c4}
table{border-collapse:separate;border-spacing:0} th,td{padding:.52rem .68rem;border-right:1px solid #ced5da;
border-bottom:1px solid #ced5da;vertical-align:top;text-align:left;min-width:12rem;width:14rem}
thead th{position:sticky;top:0;background:#243f53;color:white;z-index:2} th:first-child{position:sticky;left:0}
thead th:first-child{z-index:3} tbody th{background:#edf3f6;z-index:1;font-weight:normal}
tbody tr:nth-child(even) td{background:#f8fafb} tbody tr:target td,tbody tr:target th{outline:2px solid #6995ac}
tbody tr{scroll-margin-top:3rem} small{display:block;color:#465968;font-size:.78rem}
.sentence{white-space:pre-wrap;overflow-wrap:anywhere} ins{color:#005d44;background:#dcf7ed;text-decoration:underline}
del{color:#842a34;background:#ffeaec;text-decoration:line-through} .space{position:relative}
#marks:checked~.scroll .space:after{content:"·";position:absolute;left:0}
#marks:not(:checked)~.scroll del{display:none} #marks:not(:checked)~.scroll ins{color:inherit;background:none;text-decoration:none}
#refs:not(:checked)~.scroll .refcol,#extra:not(:checked)~.scroll .extra,#different:checked~.scroll .agree{display:none}
"""


def write_html(path, records):
    cols = {**MAIN, "reference": "배포 참조 (모델 아님)", **EXTRA}
    def col_class(k):
        return "refcol" if k == "reference" else "extra" if k in EXTRA else "model"

    with path.open("x", encoding="utf-8") as f:
        f.write('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
                'base-uri \'none\'; form-action \'none\'">'
                f'<title>Union {len(records):,}건 · 모델별 교정문</title><style>{STYLE}</style></head><body>'
                f'<h1>Union {len(records):,}건 · 원문과 실제 교정문</h1>'
                '<p>같은 표본 순서 · 문장 ID는 원래 Union 시험 순번(0부터). '
                '<del>삭제</del>는 원문에서 뺀 글자, <ins>추가</ins>는 출력에 들어간 글자입니다. '
                '변한 공백은 ·로 표시합니다. 색은 정오 판정이 아닙니다.</p>'
                '<p>「수정 표시」를 끄면 삭제 흔적 없이 실제 출력만 보입니다. '
                '같은 신경 모델의 단독(빔 4)과 + HNC(후보를 합쳐 재채점)를 비교하세요. '
                '브라우저 찾기(Ctrl/Cmd+F)로 ID나 어구를 찾을 수 있습니다.</p>')
        first = {}
        for r in records:
            first.setdefault(r["component"], r["sample_no"])
        f.write("<nav>출처별 첫 행: " + " · ".join(f'<a href="#r{n}">{DOMAINS[d]}</a>' for d,n in first.items()) + "</nav>")
        for key,label in (("marks","수정 표시"), ("different","기본 5개 출력이 일치하지 않는 행만"),
                          ("refs","말뭉치 참조 보기"), ("extra","중간 조건 4개 보기 (신경 후보만 재채점 / 신경 점수 제외)")):
            checked = " checked" if key == "marks" else ""
            f.write(f'<input type="checkbox" id="{key}"{checked}><label for="{key}">{label}</label>')
        f.write('<div class="scroll" role="region" aria-label="문장 대조표" tabindex="0"><table><thead><tr>'
                '<th scope="col">표본 · 출처 · 원문</th>')
        for k,title in cols.items():
            f.write(f'<th class="{col_class(k)}" scope="col">{escape(title)}</th>')
        f.write("</tr></thead><tbody>")
        for r in records:
            x, n = r["source"], r["sample_no"]
            state = "agree" if len({r[k] for k in MAIN}) == 1 else "disagree"
            f.write(f'<tr id="r{n}" class="{state}"><th data-key="source" scope="row">'
                    f'<small><a href="#r{n}">#{n:04d}</a> · {DOMAINS[r["component"]]} · {escape(r["unit_id"])}</small>'
                    f'<div class="sentence">{escape(x)}</div></th>')
            for k in cols:
                y = r[k]
                note = ""
                if y == "":
                    note = "<small>빈 출력</small>"
                elif x == y:
                    note = "<small>원문 그대로</small>"
                elif "".join(x.split()) == "".join(y.split()):
                    note = "<small>공백만 변경</small>"
                f.write(f'<td data-key="{k}" class="{col_class(k)}"><div class="sentence">{diff_html(x, y)}</div>{note}</td>')
            f.write("</tr>\n")
        f.write("</tbody></table></div></body></html>\n")


def main():
    records = comparisons()
    OUT.mkdir(mode=0o700, exist_ok=False)
    with (OUT/"sentences.jsonl").open("x", encoding="utf-8") as f:
        for r in records:
            line(f, r)
    write_html(OUT/"comparison.html", records)
    save(OUT/"receipt.json", {"occurrences":len(records), "sample_sha":IDS_SHA, "source_sha":SOURCE_SHA,
         "pair_sha":PAIR_SHA, "seals":{**SEALS,"haiku":HAIKU_SEAL},
         "all_five_verbatim_agree":sum(len({r[k] for k in MAIN}) == 1 for r in records),
         "columns":{**MAIN, **EXTRA}, "outputs":{f:sha(OUT/f) for f in ("comparison.html","sentences.jsonl")}})


if __name__ == "__main__":
    os.umask(0o077)
    main()
