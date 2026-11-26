import pytest

from scripts.learner_curve import budgets


def test_train_budgets_are_nested_by_id_not_source_order():
    ids = [f"training-{i}" for i in range(41)]
    b = budgets(ids)
    assert [len(b[f]) for f in (10, 25, 50, 100)] == [4, 10, 21, 41]
    assert b[10] < b[25] < b[50] < b[100] == set(ids)
    assert budgets(list(reversed(ids))) == b
    with pytest.raises(ValueError, match="unique"):
        budgets([ids[0], ids[0]])
