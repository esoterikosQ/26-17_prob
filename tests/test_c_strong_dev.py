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


def test_selection_diagnosis_uses_best_deletion_witness_and_whole_pool_ablation(monkeypatch):
    from pathlib import Path
    from scripts.c_edit_select import sentence
    tool = Path("tools/m2scorer/scripts").resolve()
    if not (tool / "levenshtein.py").is_file():
        pytest.skip("M² scorer not installed")
    monkeypatch.syspath_prepend(str(tool))
    x = "오늘 정말 새가 난다"
    rec = {"x": x, "i": 2, "unit_id": "ex", "component": "lang8"}
    hs = [{"text": t, "s": s, "h": h, "ch": ch, "s2s": seq, "lm": lm, "causal": lp, "prior": .5*lm+lp}
          for t, s, h, ch, seq, lm, lp in [
              ("오늘 새가 잔다", 0, None, 6, -4, -4, -4), (x, None, 0, .2, -6, -4, -4),
              ("오늘 새가 난다", 1, None, 4, -4, -4, -3), ("오늘 정말 새가 잔다", 2, None, 12, -2, -2, -1)]]
    _, _, ds, ins = sentence((rec, hs, ["S " + x, "A 1 2|||DELETION|||-NONE-|||REQUIRED|||-NONE-|||0"]))
    assert not ins
    d, = ds
    assert (d["state"], d["winner_action"], d["winner"], d["witness"], d["witness_rank"]) == (
        "missed", "unchanged", 1, 2, 2)
    assert (d["dominant"], d["s_hit"], d["without_channel"]) == ("channel", True, False)
    assert d["total"] == pytest.approx(1.8) and d["isolated_total"] == d["total"]
    assert (d["channel"], d["lm_prior"], d["seq"]) == pytest.approx((3.8, -1., -1.))


def test_missed_insertion_separates_wrong_boundary_from_wrong_form():
    from scripts.c_edit_select import insertion_case
    x = "새가 난다"
    hs = [{"text": "막 새가 난다", "s": 0, "h": None}, {"text": "새가 잘 난다", "s": None, "h": 0}]
    ops = {0: [(0, 0, "", "막")], 1: [(1, 1, "", "잘")]}
    case = insertion_case(x, 0, (1, 1, "", ["막"]), hs, {0: set(), 1: set()}, ops, 0)
    assert (case["coverage"], case["s"], case["h"], case["union"]) == (
        "neither", "no_boundary_insertion", "other_form", "other_form")
    assert not case["already_in_x"] and case["in_s_surface"] and not case["in_h_surface"]
    hs[1]["text"], ops[1] = "새가 막 난다", [(1, 1, "", "막")]
    case = insertion_case(x, 0, (1, 1, "", ["막"]), hs, {0: set(), 1: {0}}, ops, 1)
    assert case["selected"] and case["coverage"] == "h_only" and case["union"] == "hit"


def test_split_filter_excludes_rows_before_feature_scoring(tmp_path):
    from scripts.c_strong_dev import scored_pools
    recs = [{"i": i, "unit_id": str(i), "x": "x", "archived": "x"} for i in range(2)]
    good = {"text": "x", "s": 0, "h": None, "ch": 1, "lm": -2, "s2s": -3}
    (tmp_path / "causal.jsonl").write_text(json.dumps({"text": "x", "lp": -4}) + "\n")
    (tmp_path / "features.jsonl").write_text("".join(json.dumps({**r, "hyps": hs}) + "\n"
                                                    for r, hs in zip(recs, ([], [good]), strict=True)))
    [(rec, [hyp])] = list(scored_pools(tmp_path, recs, include=lambda r: r["i"] == 1))
    assert rec["i"] == 1 and hyp["causal"] == -4 and hyp["prior"] == -5
