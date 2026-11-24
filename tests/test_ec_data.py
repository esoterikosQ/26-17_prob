"""A-0c 쌍 분류·분할 규칙 검증 (예문은 직접 작성)."""

import pytest

from hnc.ec_data import classify, split_of


@pytest.mark.parametrize("x, y, cat", [
    ("", "안녕하세요.", "empty"),
    ("안녕 {emoji:♡}", "안녕.", "placeholder"),
    ("&name1& 안녕", "안녕", "placeholder"),
    ("안녕하세요.", "안녕하세요.", "identical"),
    ("안녕 하세요.", "안녕하세요.", "space_only"),
    ("밥먹었어?", "밥 먹었어?", "space_only"),
    ("안녕하세요!", "안녕하세요.", "punct_only"),
    ("네", "네.", "punct_only"),
    ("안녕 하세요!", "안녕하세요.", "space_punct"),
    ("하려구요", "하려고요.", "content"),
    ("못생겻다", "못생겼다", "content"),
    ("2시", "두 시", "content"),
])
def test_classify(x, y, cat):
    assert classify(x, y) == cat


def test_split_is_deterministic_and_balanced():
    ids = [f"DOC{i:06d}" for i in range(20000)]
    splits = [split_of(d) for d in ids]
    assert splits == [split_of(d) for d in ids]
    frac = {s: splits.count(s) / len(ids) for s in ("train", "dev", "test")}
    assert abs(frac["train"] - 0.90) < 0.01
    assert abs(frac["dev"] - 0.05) < 0.01
    assert abs(frac["test"] - 0.05) < 0.01


def test_placeholder_check_can_be_disabled():
    assert classify("<책> 읽었다", "<책>을 읽었다.", check_placeholder=False) == "content"
