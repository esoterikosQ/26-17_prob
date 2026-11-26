from pathlib import Path

import pytest

from scripts import final_raw_score as score
from scripts.decode import m2_blocks


@pytest.mark.skipif(not Path("tools/m2scorer/scripts/m2scorer.py").exists(), reason="requires pinned official CLI")
def test_raw_loader_retains_original_grouping_and_filters_by_official_offset(tmp_path):
    p=tmp_path/"raw.m2"
    p.write_text("S red  leaf\nA 2 3|||DELETION||||||REQUIRED|||-NONE-|||0\n\n"
                 "S one two\nA 1 2|||SPELL|||blue|||REQUIRED|||-NONE-|||0\n"
                 "S three\nA 0 1|||NOUN|||four|||REQUIRED|||-NONE-|||0\n\n")
    records,info=score.raw_rows(p,m2_blocks(p),tmp_path)
    assert [v[1] for v in records]==[[],["SPELL","NOUN"],["SPELL","NOUN"]]
    assert info["official_effective"]==4 and info["distributed_nonnoop"]==3
    assert info["invalid_physical_edits"]==[{"i":0,"range":[2,3],"type":"DELETION"}]
    assert info["multi_source_paragraphs"]==[[1,2]]
