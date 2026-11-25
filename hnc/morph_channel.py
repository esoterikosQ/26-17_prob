"""A-3 형태소 채널 — 편집 구간의 형태소 편집 추출과 자동 오류유형 z.

설계서 §1.2(a): P_M(m′|m) = Π_i Σ_z P(z_i|c_i) P(m′_i|m_i, z_i).
m = 교정문 y의 형태소(A-0d 정렬에서 편집 구간에 투영), m′ = 쓴 사람이 의도한(자모 잡음 전) 형태소.
학습 자료에서 m′는 관측되지 않으므로, 오류문 x의 같은 구간을 Kiwi로 분석한 형태소를 m′의 근사로 쓴다
(자모 잡음이 없으면 x = R(m′)이라 정확하고, 오타가 섞이면 엉뚱한 분석이 나온다 — 그 경우를 SPELL로 돌림).

방향은 생성 방향(m → m′)이다:
- ``DEL``: y의 형태소가 x에서 빠짐(쓴 사람이 빠뜨림; KAGAS ``INSERTION``에 대응),
- ``INS``: x에 형태소가 더해짐(쓴 사람이 더 씀; KAGAS ``DELETION``에 대응).

z 목록: IDENT(형태소 같음) · SPELL(내용 형태소의 표기 오류 — 자모 채널 몫) · PARTICLE(조사 대치) ·
ENDING(어미 대치) · LEXICAL(어휘 대치) · VARIANT(통용 변이의 규범화 — A-0d 검수에서 사용자가 지적한
부류: 외래어 표기, 구어 부사, 채팅 약어 등) · DEL · INS.
VARIANT 판정(``variant_by_freq``) = (1) 원문 표기가 정문 말뭉치(신문 + 교정문)에서 **어절로 충분히 쓰이고**
(≥ 5회, 교정 표기 빈도의 0.1배 이상; 어절 빈도로 판정이 안 되면 홀로 쓰이는 형태소 형태끼리 비교)
**그리고** (2) 표기 변이(자모 몇 개 차이: 아울렛/아웃렛, 것/거)이거나 모어 화자 자료(EC)에서 체계적으로
규범화되는 쌍(``systematic``: EC에서 3회 이상, 예: 완전 → 정말). (1)만 쓰면 학습자의 실재어 오용(나 → 저,
오 → 가)까지 변이로 잡힌다(Kor-Learner 실측: KAGAS 어휘 오류의 47%). 형태소 단위 빈도는 쓰지 않는다 —
Kiwi가 오타도 실재 형태소로 분석하므로(잇어서 → 잇/NNG) 오타와 변이가 구별되지 않는다(2026-11-25 실측).
- 표기 오류의 구간 판정(``surface_spell``): 오타가 든 x는 Kiwi가 엉뚱하게 쪼개므로(날시 → 날/NNG + 시/NNG)
  내용 형태소가 달라도 구간 표층이 자모 몇 개 차이면 구간 전체를 SPELL(m′ = m, 자모 채널 몫)로 돌린다.
  조사·어미만 다른 경우(을 ↔ 를)는 그대로 둔다.
문장부호 형태소(S* 중 문자·숫자가 없는 것)는 비교 전에 뗀다(A-0d 검수: 부호가 구간에 흡수되는 경우).
ㅇㅇ·ㅋㅋ처럼 한글 자모로 된 SW는 내용 형태소로 둔다.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import jamo

M = Tuple[str, str]                       # (형태, 품사)
PUNCT_TAGS = {"SF", "SP", "SS", "SSO", "SSC", "SE", "SO", "SW"}
Z_LABELS = ("IDENT", "SPELL", "PARTICLE", "ENDING", "LEXICAL", "VARIANT", "DEL", "INS")


FREE_TAGS = ("NN", "NP", "NR", "MAG", "MAJ", "MM", "XR", "IC", "SW", "SL")


def tag_class(tag: str) -> str:
    """'J'(조사) | 'E'(어미) | 'S'(부호류 품사) | 'C'(그 밖의 내용·기능 형태소)."""
    if tag.startswith("J"):
        return "J"
    if tag.startswith("E"):
        return "E"
    if tag in PUNCT_TAGS:
        return "S"
    return "C"


def is_punct(m: M) -> bool:
    return tag_class(m[1]) == "S" and not any(ch.isalnum() for ch in m[0])


def strip_punct(ms: Sequence[M]) -> List[M]:
    return [m for m in ms if not is_punct(m)]


def mclass(m: M) -> str:
    """정렬·분류용 부류: 자모·문자가 든 SW(ㅇㅇ, ㅋㅋ)는 내용(C)."""
    c = tag_class(m[1])
    return "C" if c == "S" else c


def _sub_cost(a: M, b: M) -> float:
    if a == b:
        return 0.0
    ca, cb = mclass(a), mclass(b)
    if ca == cb:
        return 1.0 if a[1] == b[1] or ca != "C" else 1.2
    return 2.5


def align_morphs(my: Sequence[M], mx: Sequence[M]) -> List[Tuple[Optional[M], Optional[M]]]:
    """형태소열 레벤슈타인 정렬(같은 부류끼리 대치가 싸다). 반환: [(m, m′)], 한쪽 None = 삭제·삽입."""
    n, k = len(my), len(mx)
    D = [[0.0] * (k + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        D[i][0] = float(i)
    for j in range(1, k + 1):
        D[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, k + 1):
            D[i][j] = min(D[i - 1][j - 1] + _sub_cost(my[i - 1], mx[j - 1]), D[i - 1][j] + 1.0, D[i][j - 1] + 1.0)
    out = []
    i, j = n, k
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + _sub_cost(my[i - 1], mx[j - 1]):
            out.append((my[i - 1], mx[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and D[i][j] == D[i - 1][j] + 1.0:
            out.append((my[i - 1], None))
            i -= 1
        else:
            out.append((None, mx[j - 1]))
            j -= 1
    return out[::-1]


def jamo_distance(a: str, b: str) -> int:
    xa, xb = jamo.decompose(a), jamo.decompose(b)
    prev = list(range(len(xb) + 1))
    for i, ca in enumerate(xa, 1):
        cur = [i] + [0] * len(xb)
        for j, cb in enumerate(xb, 1):
            cur[j] = min(prev[j - 1] + (ca != cb), prev[j] + 1, cur[j - 1] + 1)
        prev = cur
    return prev[-1]


def is_spelling_variant(a: str, b: str) -> bool:
    """두 형태가 자모 몇 개 차이(≤ 2, 그리고 긴 쪽 자모 수의 1/3 이하)면 표기 오류로 본다."""
    d = jamo_distance(a, b)
    n = max(len(jamo.decompose(a)), len(jamo.decompose(b)))
    return d <= 2 and d * 3 <= max(n, 3)


Variant = Callable[[M, M], bool]


def variant_by_freq(freq: Callable[[str], int], ey: str, ex: str, systematic=frozenset(),
                    min_count: int = 5, ratio: float = 0.1) -> Variant:
    """VARIANT 판정 함수: 구간이 든 어절 표기(교정 ``ey``, 원문 ``ex``, 부호 뗀 것), 빈도표 ``freq``,
    모어 화자 자료에서 체계적으로 규범화되는 (m, m′) 쌍 집합 ``systematic``."""
    def attested(a: int, b: int) -> bool:
        return a >= min_count and a >= ratio * b

    fy, fx = freq(ey), freq(ex)
    eojeol_ok = fy >= min_count or fx >= min_count

    def common(m: M, m2: M) -> bool:
        if eojeol_ok:
            return attested(fx, fy)
        if m[1].startswith(FREE_TAGS) and m2[1].startswith(FREE_TAGS):
            return attested(freq(m2[0]), freq(m[0]))
        return False

    def judge(m: M, m2: M) -> bool:
        return common(m, m2) and (is_spelling_variant(m[0], m2[0]) or (tuple(m), tuple(m2)) in systematic)
    judge.common = common
    return judge


def classify_edit(m: Optional[M], m2: Optional[M], variant: Variant = lambda a, b: False) -> str:
    """형태소 편집 하나의 자동 z. ``variant(m, m′)``: 내용 형태소 대치가 통용 변이의 규범화인가."""
    if m is not None and m2 is not None:
        if m == m2:
            return "IDENT"
        c1, c2 = mclass(m), mclass(m2)
        if c1 == c2 == "J":
            return "PARTICLE"
        if c1 == c2 == "E":
            return "ENDING"
        if c1 == c2 == "C" and variant(m, m2):
            return "VARIANT"
        if is_spelling_variant(m[0], m2[0]):
            return "SPELL"
        return "LEXICAL"
    if m is not None:
        return "DEL"
    return "INS"


def morph_edits(my: Sequence[M], mx: Sequence[M], variant: Variant = lambda a, b: False) -> List[dict]:
    """구간의 (y 형태소, x 형태소) → 편집 목록. 부호를 뗀 뒤 정렬하고, 같은 형태소는 IDENT로 남긴다.

    형태소열이 같은데 표층만 다르면(자모 잡음·실현 오류) 모든 항목이 IDENT이다(``region_z``가 SPELL로 돌림).
    """
    pairs = align_morphs(strip_punct(my), strip_punct(mx))
    return [{"m": a, "m2": b, "z": classify_edit(a, b, variant)} for a, b in pairs]


def surface_spell(edits: Sequence[dict], y_core: str, x_core: str) -> List[dict]:
    """내용 형태소가 달라졌는데 구간 표층(공백·부호 뗀 것)이 자모 몇 개 차이면 오타로 인한 오분석으로 보고
    m′ = m(모두 IDENT, 삽입 제거)으로 바꾼다. 조사·어미만 달라진 경우는 그대로 둔다."""
    content_changed = any(e["z"] in ("LEXICAL", "SPELL") or
                          (e["z"] in ("DEL", "INS") and mclass(e["m"] or e["m2"]) == "C") for e in edits)
    if content_changed and y_core and x_core and is_spelling_variant(y_core, x_core):
        return [{"m": e["m"], "m2": e["m"], "z": "IDENT"} for e in edits if e["m"] is not None]
    return list(edits)


def region_family(edits: Sequence[dict]) -> str:
    """KAGAS 대조용 구간 부류: 조사가 관여(대치·삭제·삽입)하면 PARTICLE, 어미면 ENDING, 그 밖의 내용 형태소
    대치는 LEXICAL(VARIANT 포함), 내용 형태소 삭제·삽입은 DEL·INS, 형태소가 같으면 SPELL."""
    ne = [e for e in edits if e["z"] != "IDENT"]
    for cls, fam in (("J", "PARTICLE"), ("E", "ENDING")):
        if any(mclass(e["m"] or e["m2"]) == cls or (e["m2"] is not None and mclass(e["m2"]) == cls) for e in ne):
            return fam
    zs = {e["z"] for e in ne}
    if zs & {"LEXICAL", "VARIANT"}:
        return "LEXICAL"
    for z in ("DEL", "INS", "SPELL"):
        if z in zs:
            return z
    return "SPELL"


def region_z(edits: Sequence[dict]) -> str:
    """구간 대표 z: IDENT가 아닌 편집의 z(여럿이면 우선순위 PARTICLE > ENDING > LEXICAL > VARIANT >
    DEL > INS > SPELL), 모두 IDENT면 SPELL(형태소는 같고 표층만 다름 → 자모 채널 몫)."""
    order = ("PARTICLE", "ENDING", "LEXICAL", "VARIANT", "DEL", "INS", "SPELL")
    zs = {e["z"] for e in edits if e["z"] != "IDENT"}
    for z in order:
        if z in zs:
            return z
    return "SPELL"


KAGAS_TO_Z: Dict[str, str] = {
    "PARTICLE": "PARTICLE", "ENDING": "ENDING", "NOUN": "LEXICAL", "VERB": "LEXICAL", "ADJECTIVE": "LEXICAL",
    "MODIFIER": "LEXICAL", "INSERTION": "DEL", "DELETION": "INS", "SPELL": "SPELL", "CONJUGATION": "SPELL",
    "SHORTEN": "SPELL", "WS": "WS", "PUNCT": "PUNCT", "WO": "WO", "UNCLASSIFIED": "UNK",
}
