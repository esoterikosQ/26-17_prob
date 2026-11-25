import json

import pytest

from scripts.c_strong_dev import output_choices, union_pairs


def test_union_separator_is_not_an_example():
    assert union_pairs(["", "글  하나\t글 하나", "가\t"]) == [("글 하나", "글 하나"), ("가", "")]
    with pytest.raises(ValueError, match="malformed"):
        union_pairs(["가\t나\t다"])
    with pytest.raises(ValueError, match="malformed"):
        union_pairs(["not a separator"])


def test_frozen_reranker_separates_source_and_scoring_effect(tmp_path):
    rec = {"i": 0, "unit_id": "a", "x": "x", "archived": "a"}
    hyps = [{"text": t, "s": s, "h": h, "ch": ch, "lm": 0, "s2s": score}
            for t, s, h, ch, score in [("a", 0, None, 9, -2), ("b", 1, None, 2, -1),
                                        ("c", None, 0, 0, -1)]]
    (tmp_path / "features.jsonl").write_text(json.dumps({**rec, "hyps": hyps}) + "\n")
    (tmp_path / "causal.jsonl").write_text("".join(json.dumps({"text": h["text"], "lp": -1}) + "\n" for h in hyps))
    assert list(output_choices(tmp_path, [rec])) == [
        {"archived": "a", "s_fixed": "b", "union_no_s2s": "c", "union_fixed": "c"}]
    with pytest.raises(ValueError, match="baseline"):
        list(output_choices(tmp_path, [{**rec, "archived": "different"}]))
