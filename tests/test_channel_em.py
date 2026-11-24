"""A-1c EM 검증: 전·후진 계산(손 계산과 대조), MAP 목적함수 단조 증가, 디코더용 확률 변환."""

import pytest

from hnc.channel_em import em, forward_backward, initial_inventory, normalize, params_from_counts

D = {(("a",), ("a",)): .5, (("b",), ("c",)): .2, (("a", "b"), ("a", "c")): .1,
     (("b",), ()): .05, ((), ("c",)): .05}


def test_forward_backward_matches_hand_computation():
    Z, ec = forward_backward(("a", "b"), ("a", "c"), D)
    # 분할: a|b→a|c (.5×.2), ab→ac (.1), a|b→∅|∅→c (.5×.05×.05), a|∅→c|b→∅ (.5×.05×.05)
    assert Z == pytest.approx(0.1 + 0.1 + 2 * 0.5 * 0.05 * 0.05)
    assert ec[(("a", "b"), ("a", "c"))] == pytest.approx(0.1 / Z)
    assert ec[(("a",), ("a",))] == pytest.approx((0.1 + 2 * 0.00125) / Z)
    assert forward_backward(("a",), ("z",), D) == (0.0, {})


def test_em_objective_is_monotone():
    windows = [(("a", "b"), ("a", "c"), 3), (("b",), ("c",), 2), (("a", "b"), ("a",), 1)]
    init = {k: 1.0 for k in D}
    _, hist = em(windows, {"a": 5}, init, iterations=6, prior=0.01, log=lambda r: None)
    obj = [h["objective"] for h in hist]
    assert all(b >= a - 1e-9 for a, b in zip(obj, obj[1:]))
    assert hist[0]["unreachable"] == 0


def test_params_from_counts_with_jamo():
    edits = [{"a": ["ᆻ"], "b": ["ᆺ"], "c": 10}, {"a": ["<SP>"], "b": [], "c": 30}]
    alphas = [{"a": ["ᆻ"], "c": 100}, {"a": ["<SP>"], "c": 100}, {"a": [], "c": 1000}]
    inv = initial_inventory(edits, alphas)
    assert inv[(("ᆻ",), ("ᆻ",))] == 90.0 and inv[(("ᆻ",), ("ᄀ",))] == 0.01
    assert abs(sum(normalize(inv, 0.01).values()) - 1.0) < 1e-9
    p = params_from_counts({(("ᆻ",), ("ᆺ",)): 8.5, (("<SP>",), ()): 31.0}, alphas)
    assert 0 < p.edits[(("ᆻ",), ("ᆺ",))] < 0.1 and p.identity["ᆻ"] > 0.9
