import hashlib
import json

import pytest

from scripts import score_blind_review as blind
from scripts.c_eval import blind_sample


def prediction(code, z, post=None, *, back=False):
    return {"id": code, "z": z, "backoff_only": back, "posterior": {z: 1.} if post is None else post}


def test_denominators_include_invalid_proposals_and_model_abstentions():
    human = {"a": ("타당", "PARTICLE"), "b": ("부적절", "PARTICLE"), "c": ("둘 다 허용", "VARIANT"),
             "d": ("타당", "ENDING"), "e": ("판단 어려움", "NO_ERROR"), "f": ("미응답", "MISSING")}
    keys = [prediction("a", "PARTICLE_INS"), prediction("b", "UNCLASSIFIED", back=True),
            prediction("c", "VARIANT"), prediction("d", "MORPH_DEL"), prediction("e", "UNCLASSIFIED"),
            prediction("f", "WS")]
    r = blind.summarize(human, list(reversed(keys)))
    assert r["type_agreement"] == {"count": 2, "denominator": 4, "rate": .5}
    assert r["type_agreement_valid_only"]["rate"] == .5
    assert r["valid_strict"]["rate"] == .5 and r["acceptable_including_variation"]["rate"] == .75
    assert r["typed_coverage"]["count"] == 2 and r["calibration"]["ece"] is None
    assert r["backoff_only"]["count"] == 1 and r["map_unclassified"]["count"] == 2
    assert sum(c for _, _, c in r["confusion_human_model"]) == 6
    assert ["ENDING", "UNSPECIFIED_MORPH", 1] in r["confusion_human_model"]
    with pytest.raises(ValueError, match="key IDs"):
        blind.summarize(human, [keys[0], *keys[:-1]])


def test_confidence_uses_stored_map_family_not_a_new_family_argmax():
    human, keys = {}, []
    for i in range(50):
        code = str(i)
        human[code] = "타당", "PARTICLE"
        if i < 25:
            keys.append(prediction(code, "PARTICLE", {"PARTICLE": .45, "PARTICLE_DEL": .15, "WS": .4}))
        else:
            keys.append(prediction(code, "JAMO", {"JAMO": .4, "PARTICLE": .35, "PARTICLE_INS": .25}))
    r = blind.summarize(human, keys)
    assert r["type_agreement"]["count"] == 25
    assert [b["n"] for b in r["calibration"]["bins"]] == [25, 25, 0]
    assert r["calibration"]["ece"] == pytest.approx(.4)


def test_renderer_roundtrip_and_blind_validation_before_key(tmp_path, monkeypatch):
    # Independent synthetic records; never use a person's form, archived key, or source sentence.
    edit = {"x": [0, 1], "y": [0, 1], "z": "WS", "posterior": {"WS": 1.}, "backoff_only": False}
    records = [{"unit_id": str(i), "x": "초록", "hyps": [{"text": "파랑", "edits": [edit]}]} for i in range(100)]
    blind_sample(tmp_path, records, range(100), [0]*100)
    template, key, form = (tmp_path/name for name in ("review_blind.md", "review_key.jsonl", "completed.md"))
    out = tmp_path / "scores.json"
    monkeypatch.setattr(blind, "TEMPLATE_SHA", hashlib.sha256(template.read_bytes()).hexdigest())
    monkeypatch.setattr(blind, "KEY_SHA", hashlib.sha256(key.read_bytes()).hexdigest())
    text = template.read_text()
    with pytest.raises(ValueError, match="declare completion"):
        blind.main(form, template, key, out, review_complete=False)
    for bad, message in ((text, "no human judgments"),
                         (text.replace("## C4-002", "## C4-001"), "duplicate"),
                         (text.replace("## C4-100", "## C4-101"), "missing review IDs"),
                         (text.replace('- 원문 x: "초록"', '- 원문 x: "파랑"', 1), "region changed"),
                         (text.replace("- 교정 판정: ", "- 교정 판정: 별도 의견", 1), "unrecognized")):
        form.write_text(bad)
        with pytest.raises(ValueError, match=message):
            blind.main(form, template, tmp_path / "no-key-to-read", out, review_complete=True)
    form.write_text(text.replace("- 교정 판정: ", "- 교정 판정: 둘 다 허용(문체·규범 선택)", 1)
                    .replace("- 원문 오류유형: ", "- 원문 오류유형: 조사(누락)", 1))
    blind.main(form, template, key, out, review_complete=True)
    r = json.loads(out.read_text())
    assert r["regions"] == 100 and r["validity"]["미응답"]["count"] == 99
    assert ["PARTICLE", "WS", 1] in r["confusion_human_model"] and r["type_agreement"]["rate"] == 0
    assert r["type_agreement_valid_only"]["rate"] is None
    with pytest.raises(FileExistsError):
        blind.main(form, template, key, out, review_complete=True)
