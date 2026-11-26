import json

import pytest

from scripts import rehearsal_seal as seal
from scripts import rehearsal_score as score
from scripts.rehearsal_aux import edit_groups, ratios, typed_counts, typed_partition
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


def test_typed_matching_uses_allowed_surfaces_spans_types_and_each_edit_only_once():
    def entry(t, correction, pos="0 1"):
        return f"A {pos}|||{t}|||{correction}|||REQUIRED|||-NONE-|||0"
    source = "S 두 송이"
    gold = edit_groups([source, entry("SPELL", "셋||세"), entry("SPELL", "셋"), entry("ENDING", "넷")])
    predicted = edit_groups([source, entry("SPELL", "셋"), entry("SPELL", "세"), entry("SPELL", "셋"),
                             entry("NOUN", "넷"), entry("ENDING", "넷", "1 2")])
    h, p, g = typed_counts(gold, predicted)
    assert h["SPELL"] == 2 and h.total() == 2 and p.total() == 5 and g.total() == 3
    assert ratios(0, 0, 2) == {"tp": 0, "proposed": 0, "gold": 2, "P": None, "R": 0., "F0.5": 0.}
    assert ratios(0, 0, 0)["F0.5"] is None
    with pytest.raises(ValueError, match="different source"):
        typed_counts(gold, edit_groups(["S 한 송이"]))


def test_one_unannotatable_pair_invalidates_its_entire_cell_not_other_systems_or_partitions():
    counts = typed_counts(edit_groups(["S 푸른 바다"]), edit_groups(["S 푸른 바다"]))
    evidence = [{"r9": counts, "r11": counts}, {"r9": counts, "r11": None}]
    assert typed_partition(evidence, [0, 1], "r11", {"WS"}) == (None, [1])
    for ids, system in (([0, 1], "r9"), ([0], "r11")):
        valid, failed = typed_partition(evidence, ids, system, {"WS"})
        assert valid == {"WS": ratios(0, 0, 0)} and failed == []
