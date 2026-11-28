from html.parser import HTMLParser

import pytest

from scripts.union_sample_side_by_side import EXTRA, MAIN, haiku_texts, write_html


class DisplayedCells(HTMLParser):
    """Recover the before/after strings from the sentence elements, not the export JSON."""

    def __init__(self):
        super().__init__()
        self.table = {}
        self.row = self.key = self.edit = None
        self.in_sentence = False

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "tr" and "id" in attr:
            self.row = attr["id"]
            assert self.row not in self.table
            self.table[self.row] = {}
        elif tag in ("td", "th") and "data-key" in attr:
            self.key = attr["data-key"]
            assert self.key not in self.table[self.row]
            self.table[self.row][self.key] = ["", ""]
        elif tag == "div" and attr.get("class") == "sentence":
            self.in_sentence = True
        elif self.in_sentence:
            if tag in ("ins", "del"):
                self.edit = tag
            else:
                assert tag == "span" and attr == {"class": "space"}

    def handle_endtag(self, tag):
        if tag == "div":
            self.in_sentence = False
        elif tag in ("ins", "del"):
            self.edit = None

    def handle_data(self, text):
        if self.in_sentence:
            cell = self.table[self.row][self.key]
            if self.edit != "ins":
                cell[0] += text
            if self.edit != "del":
                cell[1] += text


def test_html_recovers_verbatim_sentences_with_empty_unchanged_and_whitespace_edits(tmp_path):
    x = "  <단풍> & 잎  "
    r = dict.fromkeys([*MAIN, *EXTRA], x)
    r.update({"sample_no": 1, "unit_id": "union:test:4", "component": "native", "source": x,
              "reference": "단풍잎", "kobart/r9": "", "kobart/r11": x.strip(), "t5/r9": x + "!"})
    path = tmp_path / "view.html"
    write_html(path, [r])
    display = DisplayedCells()
    display.feed(path.read_text(encoding="utf-8"))
    display.close()
    assert display.table == {"r1": {k: [x, r[k]] for k in ("source", "reference", *MAIN, *EXTRA)}}


def test_join_rejects_repeated_missing_and_reordered_occurrences_without_filling_empty():
    a = {"unit_id": "union:test:4", "prediction": "  "}
    b = {"unit_id": "union:test:8", "prediction": ""}
    assert haiku_texts([4, 8], [a, b]) == ["  ", ""]
    for bad in ([a, a], [b, a], [a], [a, b, b]):
        with pytest.raises(ValueError):
            haiku_texts([4, 8], bad)
