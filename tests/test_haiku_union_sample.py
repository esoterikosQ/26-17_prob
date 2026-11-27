import pytest

from scripts import haiku_union_sample as sample


def test_submission_requires_exact_order_and_keeps_empty_predictions():
    keys = ["U0001", "U0002"]
    assert sample.parse_batch("U0001\t겹친다.\nU0002\t\n", keys) == ["겹친다.", ""]
    for bad in ("U0002\t가\nU0001\t나", "U0001\t가", "U0001\t가\nU0002\t나\t메모"):
        with pytest.raises(ValueError):
            sample.parse_batch(bad, keys)


def test_presentation_hides_original_order_but_preserves_all_fixed_occurrences():
    ids, _ = sample.sample()
    shuffled = sample.order()
    assert sorted(shuffled) == ids and shuffled != ids
    labels = [k for b in range(sample.SIZE//sample.WIDTH) for k in sample.batch_keys(b)]
    assert len(set(labels)) == len(shuffled) == 1000
    with pytest.raises(ValueError):
        sample.batch_keys(-1)
