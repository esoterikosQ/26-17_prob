import json

import pytest

from scripts import rehearsal_seal as seal
from scripts import rehearsal_score as score
from scripts.rehearsal_predict import require_source, systems


def test_sources_reject_reference_or_archived_prediction_and_preserve_occurrences():
    row = {"i": 2, "unit_id": "union:dev:2", "x": "작은 꽃"}
    require_source(row, "union", 2)
    for extra in ({"y": "정답"}, {"archived": "과거 가설"}, {"component": "native"}):
        with pytest.raises(ValueError, match="only occurrence"):
            require_source({**row, **extra}, "union", 2)
    with pytest.raises(ValueError):
        require_source(row, "union", 1)


def test_global_seal_rejects_partial_as_full_and_modified_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(seal, "CONFIG", {"korean_learner": (1, 9, 54.5049, "")})
    d = tmp_path / "korean_learner"
    d.mkdir()
    for name in seal.inventory(True):
        (tmp_path / name).write_text("{}\n")
    src = {"i": 0, "unit_id": "korean_learner:dev:0", "x": "새가 난다"}
    (d / "sources.jsonl").write_text(json.dumps(src) + "\n")
    pred = {**src, "predictions": dict.fromkeys(systems("korean_learner", True), "새가 난다")}
    path = d / "predictions.partial.jsonl"
    path.write_text(json.dumps(pred) + "\n")
    fingerprint = seal.check_seal(tmp_path, True, create=True)
    assert seal.check_seal(tmp_path, True) == fingerprint
    assert json.loads((tmp_path / "seal.partial.json").read_text())["omitted_learner_rows"] == [1, 5, 6, 7]
    with pytest.raises(FileNotFoundError):
        seal.check_seal(tmp_path, False)
    path.write_text(json.dumps({**pred, "x": "다른 문장"}) + "\n")
    with pytest.raises(ValueError, match="changed predictions"):
        seal.check_seal(tmp_path, True)


def test_promoting_may_only_add_new_decisions():
    partial = {"i": 0, "predictions": {"r9": "봄"}, "audit": {"r9": []}}
    full = {**partial, "predictions": {"r9": "봄", "r1": "밤"}}
    seal.unchanged_decisions([partial], [full])
    with pytest.raises(ValueError, match="sealed decision"):
        seal.unchanged_decisions([partial], [{**full, "predictions": {"r9": "밤", "r1": "밤"}}])


def test_grade_never_opens_gold_before_full_seal(monkeypatch):
    def reject(root, partial):
        assert partial is False
        raise ValueError("full seal required")
    monkeypatch.setattr(score, "check_seal", reject)
    monkeypatch.setattr(score, "gold_path", lambda d: pytest.fail("gold opened"))
    with pytest.raises(ValueError, match="full seal required"):
        score.open_dev("lang8")


def test_grade_compares_occurrences_not_just_the_normalized_sentence():
    x = "두  송이"
    p = {"i": 1, "unit_id": "lang8:dev:1", "x": x, "predictions": dict.fromkeys(systems("lang8", False), x)}
    r = {"i": 1, "unit_id": "original-corpus-id", "x": "두 송이", "y": "세 송이"}
    score.validate_join("lang8", 1, p, r, ["S 두 송이"], r["y"])
    for changed in ({"i": 0}, {"x": "다른 원문"}, {"y": "다른 참조"}):
        with pytest.raises(ValueError):
            score.validate_join("lang8", 1, p, {**r, **changed}, ["S 두 송이"], r["y"])
