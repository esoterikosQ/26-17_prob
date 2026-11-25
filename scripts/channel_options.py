"""디코딩·재채점·설명에서 같은 자모 채널과 형태소 경로 가중치를 사용한다."""

import argparse
import json
import math
from pathlib import Path

from hnc.jamo_channel import ChannelParams, JamoChannel, interpolate


def _share(s: str) -> float:
    value = float(s)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("혼합 가중치는 [0, 1] 범위")
    return value


def _mass(s: str) -> float:
    value = float(s)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("형태소 경로 가중치는 유한한 음이 아닌 수")
    return value


def _mass_grid(s: str) -> list[float]:
    values = [_mass(x) for x in s.split(",")]
    if len(values) != len(set(values)):
        raise argparse.ArgumentTypeError("형태소 경로 가중치가 중복됨")
    return values


def add_channel_options(ap: argparse.ArgumentParser, *, grid: bool = False) -> None:
    ap.add_argument("--mix-channel", type=Path, help="두 번째 집단의 자모 채널 디렉토리")
    ap.add_argument("--mix-weight", type=_share, default=0.0, help="두 번째 자모 채널의 편집별 혼합비 ρ")
    ap.add_argument("--morph-weight", type=_mass_grid if grid else _mass, default=[1.0] if grid else 1.0,
                    help="형태소 편집 경로 질량의 배수(0=끔, 1=기존)" + (", 쉼표로 격자" if grid else ""))
    ap.add_argument("--lexical-k", type=int, default=0, help="어절당 추가 일반명사·동사 대치 후보 수(0=끔)")
    ap.add_argument("--lexical-min-count", type=int, default=5, help="어휘 대치 학습 사건의 최소 빈도")


def load_channel(primary: Path, secondary: Path | None, weight: float) -> JamoChannel:
    def read(d: Path):
        p = ChannelParams.from_json((d / "params.json").read_text(encoding="utf-8"))
        return p, json.loads((d / "other_symbols.json").read_text(encoding="utf-8"))

    if not 0 <= weight <= 1:
        raise ValueError("--mix-weight는 [0, 1] 범위")
    if weight == 0:
        return JamoChannel(*read(primary))
    if secondary is None:
        raise ValueError("양의 --mix-weight에는 --mix-channel이 필요하다")
    b, others_b = read(secondary)
    if weight == 1:
        return JamoChannel(b, others_b)
    a, others_a = read(primary)
    return JamoChannel(interpolate(a, b, weight), set(others_a) | set(others_b))
