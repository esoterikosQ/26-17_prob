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


# ---------------------------------------------------------------------------
# A-3b 형태소 채널 확률 (자동 z 지도 추정)과 A-3c 디코더용 역후보
# ---------------------------------------------------------------------------
SUB_Z = ("PARTICLE", "ENDING", "LEXICAL", "VARIANT")


def _key(m) -> str:
    return f"{m[0]}/{m[1]}"


class MorphChannel:
    """P_M(m′|m)의 추정치와 역방향 후보(관측 m′ → 의도 m).

    생성 방향의 사건과 확률(y 형태소 m, 왼쪽 y 형태소 품사 lt):
    - 대치 m → m′ (z ∈ SUB_Z): P = N(m→m′) / (N(m) + β)
    - 삭제(쓴 사람이 m을 빠뜨림): P(DEL | m, lt) = (N_del(m, lt) + β·P(DEL|m)) / (N(lt, m) + β),
      P(DEL|m) = N_del(m) / (N(m) + β)
    - 삽입(쓴 사람이 m′를 더 씀): P(INS m′ | lt) = N_ins(m′, lt) / (N(lt) + β)
    - 동일: P(IDENT | m) = 1 − (대치 + 삭제)/N(m), 품사 수준 비율로 평활(β).
    디코딩 비용 증분 Δ(x를 그대로 두는 가설 대비, 자연로그 음수):
    - 대치 후보(x의 m′ 자리에 m): −log P(m→m′) + log P(IDENT | m′)
    - 삽입 제거 후보(x의 m′를 뺌): −log P(INS m′ | lt) + log P(IDENT | m′)
    - 삭제 복원 후보(lt 뒤에 m을 넣음): −log P(DEL | m, lt)
    빈도 ``min_count`` 미만 사건은 후보로 쓰지 않는다.
    """

    def __init__(self, sub, ident, ins, dele):
        self.sub = sub        # m′ 키 → [(m, z, 비용 −log P(m→m′))]
        self.ident = ident    # 형태소 키 → −log P(IDENT|m)
        self.ins = ins        # (m′ 키, lt) → −log P(INS m′|lt)
        self.dele = dele      # lt → [(m, 비용 −log P(DEL|m,lt))]
        self._tag_ident = {}
        for k, c in ident.items():
            t = k.rsplit("/", 1)[1]
            self._tag_ident.setdefault(t, []).append(c)
        self._tag_ident = {t: sorted(v)[len(v) // 2] for t, v in self._tag_ident.items()}

    # --- 추정 -----------------------------------------------------------------
    @classmethod
    def fit(cls, regions: Sequence[dict], uni: Dict[Tuple[str, str], int], bi: Dict[Tuple[str, str, str], int],
            beta: float = 5.0, min_count: int = 2, max_alt: int = 8) -> "MorphChannel":
        import math
        from collections import Counter, defaultdict
        n_sub, n_sub_z, n_del, n_del_lt, n_ins = Counter(), defaultdict(Counter), Counter(), Counter(), Counter()
        for r in regions:
            for e in r["edits"]:
                z = e["z"]
                m = tuple(e["m"]) if e["m"] else None
                m2 = tuple(e["m2"]) if e["m2"] else None
                if z in SUB_Z:
                    n_sub[(m, m2)] += 1
                    n_sub_z[(m, m2)][z] += 1
                elif z == "DEL":
                    n_del[m] += 1
                    n_del_lt[(m, e["lt"])] += 1
                elif z == "INS":
                    n_ins[(m2, e["lt"])] += 1
        n_tag = Counter()
        for (f, t), n in uni.items():
            n_tag[t] += n
        out_sub = Counter()
        for (m, m2), n in n_sub.items():
            out_sub[m] += n
        # 품사 수준 비동일 비율 → 형태소별 P(IDENT) 평활
        tag_nonid = Counter()
        for m, n in list(out_sub.items()) + list(n_del.items()):
            tag_nonid[m[1]] += n
        ident = {}
        for (f, t), n in uni.items():
            r_tag = min(tag_nonid[t] / n_tag[t], 0.99) if n_tag[t] else 0.0
            nonid = out_sub.get((f, t), 0) + n_del.get((f, t), 0)
            p = (n - nonid + beta * (1 - r_tag)) / (n + beta)
            ident[_key((f, t))] = -math.log(min(max(p, 1e-6), 1.0))
        sub = defaultdict(list)
        for (m, m2), n in n_sub.items():
            if n < min_count or m is None or m2 is None:
                continue
            p = n / (uni.get(m, 0) + beta)
            sub[_key(m2)].append((list(m), n_sub_z[(m, m2)].most_common(1)[0][0], -math.log(min(p, 1.0))))
        sub = {k: sorted(v, key=lambda a: a[2])[:max_alt] for k, v in sub.items()}
        ins = {}
        for (m2, lt), n in n_ins.items():
            if n >= min_count:
                ins[f"{_key(m2)}|{lt}"] = -math.log(min(n / (n_tag[lt] + beta), 1.0))
        dele = defaultdict(list)
        for (m, lt), n in n_del_lt.items():
            if n < min_count:
                continue
            p_m = n_del[m] / (uni.get(m, 0) + beta)
            p = (n + beta * p_m) / (bi.get((lt, m[0], m[1]), 0) + beta)
            dele[lt].append((list(m), -math.log(min(p, 1.0))))
        dele = {k: sorted(v, key=lambda a: a[1])[:max_alt] for k, v in dele.items()}
        return cls(dict(sub), ident, ins, dele)

    # --- 직렬화 ---------------------------------------------------------------
    def to_json(self) -> str:
        import json
        return json.dumps({"sub": self.sub, "ident": self.ident, "ins": self.ins, "dele": self.dele},
                          ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "MorphChannel":
        import json
        d = json.loads(s)
        return cls(d["sub"], d["ident"], d["ins"], d["dele"])

    # --- 디코딩 ---------------------------------------------------------------
    def ident_cost(self, m) -> float:
        k = _key(m)
        if k in self.ident:
            return self.ident[k]
        return self._tag_ident.get(m[1], 0.0)

    def candidates(self, ms: Sequence[M], lt0: str = "BOS", max_cands: int = 12) -> List[Tuple[List[M], str, float]]:
        """어절 하나의 x 형태소열 ``ms``(부호 제외) → [(바뀐 형태소열, z, 비용 증분 Δ)] (편집 1개씩).

        ``lt0``: 어절 앞(왼쪽 어절의 마지막 형태소) 품사.
        """
        out = []
        for j, m2 in enumerate(ms):
            idc = self.ident_cost(m2)
            for m, z, c in self.sub.get(_key(m2), ()):
                out.append((list(ms[:j]) + [tuple(m)] + list(ms[j + 1:]), z, c - idc))
            lt = ms[j - 1][1] if j > 0 else lt0
            c = self.ins.get(f"{_key(m2)}|{lt}")
            if c is not None and len(ms) > 1:
                out.append((list(ms[:j]) + list(ms[j + 1:]), "INS", c - idc))
        for j in range(len(ms)):
            for m, c in self.dele.get(ms[j][1], ()):
                if j + 1 < len(ms) and tuple(ms[j + 1]) == tuple(m):
                    continue
                out.append((list(ms[:j + 1]) + [tuple(m)] + list(ms[j + 1:]), "DEL", c))
        out.sort(key=lambda a: a[2])
        return out[:max_cands]
