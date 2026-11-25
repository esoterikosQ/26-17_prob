import argparse

import pytest

from scripts.channel_options import add_channel_options
from scripts.population_dev import is_selection


def test_selection_groups_ignore_source_variations():
    assert is_selection("native", {"y": "고운  빛", "x": "다른 표현"}) == is_selection("native", {"y": "고운 빛"})
    assert is_selection("ec2", {"doc_id": "sample", "y": "가"}) == is_selection("ec2", {"doc_id": "sample", "y": "나"})


def test_cli_grid_and_single_model_defaults():
    grid, single = argparse.ArgumentParser(), argparse.ArgumentParser()
    add_channel_options(grid, grid=True)
    add_channel_options(single)
    assert grid.parse_args([]).morph_weight == [1.0]
    assert single.parse_args([]).morph_weight == 1.0
    args = grid.parse_args(["--mix-weight", "0.5", "--morph-weight", "0,0.1,1,3"])
    assert args.mix_weight == .5 and args.morph_weight == [0, .1, 1, 3]
    for argv in (["--morph-weight", "1,1"], ["--morph-weight", "inf"], ["--mix-weight", "nan"]):
        with pytest.raises(SystemExit):
            grid.parse_args(argv)
