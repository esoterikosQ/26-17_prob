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
    # Lower-rank neural-only 'b' must be ineligible, even when its channel cost is best.
    hyps[1]["ch"] = -20
    for h in hyps:
        del h["s2s"]
    (tmp_path / "features.jsonl").write_text(json.dumps({**rec, "hyps": hyps}) + "\n")
    assert list(output_choices(tmp_path, [rec], s1h8=True)) == [{"archived": "a", "s1h8_no_s2s": "c"}]
    with pytest.raises(ValueError, match="baseline"):
        list(output_choices(tmp_path, [{**rec, "archived": "different"}]))


def test_no_channel_keeps_pool_prior_and_teacher_forcing():
    from scripts.c_proposal_diag import choices
    hs = [{"text": t, "s": s, "h": h, "prior": prior, "ch": ch, "s2s": seq}
          for t, s, h, prior, ch, seq in [("a", 0, None, -4, 0, -1), ("b", 1, None, -1, 8, -2),
                                         ("c", None, 0, -3, 0, -1), ("d", None, 1, -.1, 8, -10)]]
    sel = choices(hs)
    assert (sel["s_fixed"], sel["union_fixed"]) == (0, 2)
    assert (sel["s_no_channel"], sel["union_no_channel"]) == (1, 1)
    assert hs[1]["ch"] == 8


def test_edit_fates_distinguish_absence_missed_selection_and_same_type_swaps():
    from scripts.c_proposal_diag import checked_counts, edit_fates
    hs = [{"s": s, "h": h, "tp": hits} for s, h, hits in [
        (0, None, [0, 3]), (1, None, [2, 6]), (2, 0, [0]), (None, 1, [1, 3, 6]), (None, 2, [5])]]
    chosen = {"archived": 0, "s_fixed": 0, "union_fixed": 3, "s_no_channel": 1, "union_no_channel": 4}
    fates = edit_fates(hs, ["PARTICLE", "PARTICLE", "ENDING", "WS", "SPELL", "PARTICLE", "NOUN"], chosen)
    particle = checked_counts(fates["PARTICLE"])
    assert (particle["gained"], particle["lost"], particle["gained_h_only"]) == (1, 1, 1)
    assert (particle["gold"], particle["covered_not_selected"], particle["union_fixed"]) == (3, 2, 1)
    assert particle["channel_gained"] == particle["channel_lost"] == 1
    assert fates["ENDING"]["s_only"] == fates["ENDING"]["covered_not_selected"] == 1
    assert fates["SPELL"]["neither"] == 1 and not fates["SPELL"]["covered_not_selected"]
    assert fates["WS"]["retained"] == 1
    assert fates["NOUN"]["gained"] == 1 and not fates["NOUN"]["gained_h_only"]
