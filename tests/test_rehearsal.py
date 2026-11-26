import json

import pytest

from scripts import rehearsal_seal as seal
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
