import pytest

from scripts import final_test as final
from scripts import final_test_score as score
from scripts.rehearsal_predict import require_source


def test_test_sources_preserve_repeats_and_reject_targets(monkeypatch):
    monkeypatch.setattr(final, "N", {"lang8": 2})
    r = final.source_rows("lang8", ["눈 온다", "눈 온다"])
    require_source(r[1], "lang8", 1, "test")
    assert r[0]["unit_id"] != r[1]["unit_id"]
    with pytest.raises(ValueError):
        final.source_rows("lang8", ["눈 온다\t참조", "눈 온다"])
    with pytest.raises(ValueError):
        require_source(r[1], "lang8", 1, "dev")


def test_gold_requires_the_global_test_seal(monkeypatch):
    def absent():
        raise ValueError("not sealed")
    monkeypatch.setattr(score, "seal", absent)
    monkeypatch.setattr(score, "reference", lambda d: pytest.fail("reference accessed"))
    with pytest.raises(ValueError, match="not sealed"):
        score.open_gold("union")


def test_finished_run_cannot_be_opened_again(tmp_path, monkeypatch):
    permit = tmp_path/"once"
    permit.write_text("{}")
    monkeypatch.setattr(final, "PERMIT", permit)
    monkeypatch.setattr(final, "assets", lambda: pytest.fail("checking a second run"))
    with pytest.raises(FileExistsError, match="second run"):
        final.authorize()
