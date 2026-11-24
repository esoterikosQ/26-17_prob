# 한국어 GEC 계층적 잡음 채널(HNC) 작업 로그

- 시각은 KST. 위치 표기: **[gsm]** 로컬(`phdq_prob/`), **[itcerdo]** `~/projects/phdq_prob` (명령의 상대경로는 별도 표기가 없으면 저장소 루트, `.venv` 활성화 상태).
- 기록 규칙은 `CLAUDE.md` 참조. 새 기록은 맨 아래에 시간순으로 추가한다.

---

## 2026-11-24

### 07:09–07:50 · 설계서·하위 프로젝트 검토, 방향 결정 [gsm]

- 작업: 설계서(A·B·C안)와 `phdq_noisy`(자모 채널 원형), `phdq_pcfg`(영어 PCFG), `phdq_pcfg2`(한국어 PCFG 착수 전)를 대응시켜 간극 정리: 자모 위치 표상 충돌, 채널 범위(자판 → 음운 오류 포함), 형태소→표층 실현 단계의 암묵성, $z$ 지도 데이터 부재, PCFG 재채점의 실패 점수·속도.
- 결정(사용자): 자모 초·종성 구분, PCFG 말단 = 형태소, C++ CKY 초기 이식, HNC 공통 인프라를 이 저장소에서 PCFG 트랙과 병행.
- 결과: 설계서 §1.1에 결정 반영(Kiwi, 위치 구분 자모, 겹자모 타건 분해, 표층 실현 $R$). `CLAUDE.md`, `plan.md` 작성.

### 07:52–08:20 · 원격 작업 방식 확정 [gsm, itcerdo]

- 작업: `phdq_bart2`의 운용 문서(`ssh.md`, `plan.md` §0, `plan2.md` §0, `NEURON.md`)에서 규칙 확인. 사용자 결정: itcerdo `~/projects/phdq_prob` 신규, 저장소 `esoterikosQ/26-17_prob`, NIKL 말뭉치 gsm→itcerdo 전송 허락, Kor-* 데이터·세종 말뭉치는 추후.

```bash
ssh -o BatchMode=yes itcerdo 'hostname; nproc; free -g | head -2; python3 --version; g++ --version | head -1; cmake --version | head -1; which tmux git uv'
gh repo view esoterikosQ/26-17_prob --json visibility,isEmpty
```

- 결과: itcerdo 24코어·RAM 60GB·Python 3.12.3, uv 없음. GitHub 저장소는 비어 있고 **public** → `data/`·`datasets/`·`outputs/`·PDF를 `.gitignore`로 제외.
- 제약: `phdq_noisy`, `phdq_pcfg`, `phdq_bart2`는 수정 금지(읽기 전용 참조).

### 08:20–08:24 · P0 itcerdo 환경 구성·데이터 전송 [gsm, itcerdo]

- 작업: GitHub push(`e6e0496`), itcerdo `~/projects/phdq_prob` 생성·clone, `.venv`(PIP·XDG·HF 캐시를 저장소 안으로 고정), `kiwipiepy`·`ijson`·`pytest` 설치. EC 2021·2022, MP 2025를 gsm→itcerdo `data/`로 전송.

```bash
# [itcerdo]
mkdir -p ~/projects/phdq_prob && cd ~/projects/phdq_prob && git clone https://github.com/esoterikosQ/26-17_prob.git .
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -c "from kiwipiepy import Kiwi; k=Kiwi(); print(k.join([(t.form,t.tag) for t in k.tokenize('학교에 갔었다.')]))"
# [gsm] phdq_noisy/data 에서
rsync -a NIKL_EC_2021_v1.0_JSON NIKL_EC_2022_v1.0_JSON NIKL_MP_2025_v1.0 data.md itcerdo:projects/phdq_prob/data/
# 파일별 바이트 수 대조 (gsm stat -f '%z %N' vs itcerdo stat -c '%s %n')
```

- 결과: kiwipiepy 0.25.1, `join`이 "학교에 갔었다." 복원. 전송 47초, 13개 파일 바이트 수 전부 일치. itcerdo 디스크 여유 72GB(사용률 92%) → **신문 말뭉치(7.3GB)는 형태소 LM 단계(A-2)에서 전송**하기로 보류.

### 08:13–08:25 · A-0a 자모 표상 v2, A-0b 형태소 분석·표층 실현 구현 [gsm, itcerdo]

- 작업: `hnc/jamo.py`(초·중·종성 구분 조합형 자모, 겹자모 타건 분해, `<SP>`, 한글 외 문자 통과, 조합형 자모 원문자는 `\` 접두 이스케이프, NFC 정규화, 엄격 `compose`·`is_valid`, 위치 심볼→두벌식 키 매핑 `KEY_OF`). `hnc/morph.py`(형태소 = (형태, 품사, 왼쪽 띄어쓰기), Kiwi `join`의 3튜플 입력으로 띄어쓰기까지 실현). `hnc/corpus.py`(EC·MP 리더, `phdq_noisy`에서 가져와 문서 id 추가). 커밋 `9e5a50e`.

```bash
# [itcerdo]
git pull && pytest -q
```

- 결과: **28 passed**. 11,172 완성형 음절 왕복 무결성, 알파벳 50(초성 19·중성 14·종성 16·`<SP>`), 혼합 문자열(숫자·라틴·문장부호·호환 자모·탭·줄바꿈·이모지) 왕복, 비정규 자모 배열 거부 확인. Kiwi `num_workers=0` 사용 중단 경고 → 기본값 사용으로 수정.

### 08:25–08:30 · A-0b 표층 실현 왕복률 측정 [itcerdo]

- 작업: EC 교정문·MP 문장에서 각 20,000개 표본(저수지 표집, seed 0)으로 `realize(analyze(y)) == y` 측정. EC 표식(`{emoji:}`, `&name&`, 태그) 포함 문장은 제외.

```bash
tmux new-session -d -s a0b "python scripts/realize_roundtrip.py --n 20000 --seed 0 --workers 8 > outputs/a0b/run.log 2>&1 < /dev/null"
```

- 결과 (EC 단위 1,699,106개 중 표식 포함·빈 문장 26,736개 제외):

| 표본 | 완전 일치 | 공백만 다름 | 그 외 |
|---|---|---|---|
| EC 교정문 20,000 | **94.3%** | 0.7% | 5.0% |
| MP 문장 20,000 | **83.1%** | 5.5% | 11.4% |

- 실패 유형(차이 구간 빈도 상위, 세부는 `outputs/a0b/`):
  1. **축약·비축약 자유 변이**가 최다: 원문의 축약형을 비축약으로(게→것이, 건→거인, 텐→터인, 케→하게, 치→하지), 원문의 비축약형을 축약으로(되어→돼, 되었→됐, 하여→해, 하였→했, 너의→네, 저에게→제게) 바꿈. 인용 축약(-다고 했→-댔)도 같은 부류.
  2. **한글 외 문자 뒤 이형태 선택**: 라틴 문자·괄호·숫자 뒤의 이/가, 은/는, 을/를, 과/와, 으 삽입·삭제.
  3. **공백**: 문장부호·인용부호 주변 공백 삽입·삭제, 연속 공백 축약.
  4. 서술격 조사 '이' 생략·삽입, 점자 공백(U+2800) 같은 잡음 문자.
- 해석: 형태소열은 축약 여부를 담지 않으므로 **$R$은 함수가 아니라 관계**다(같은 $m$에 표준형이 둘 이상). → `plan.md` §4에 처리 방안 기록.

### 08:30–08:37 · A-0c EC 스키마 확인·쌍 분류·고정 분할 [gsm, itcerdo]

- 작업: EC 단위 레코드 필드 확인(오류유형 주석 없음). `form`은 `original_form`에서 말뭉치 표식(`&이름&`의 `&`, `{emoji}`, `{share:photo}`, `{censored}` 등)을 지운 정제본임을 차이 구간 집계로 확인 → **x = `form`, y = `corrected_form`**. 메신저 자료의 화자 메타데이터에 `device`·`keyboard`가 있어 레코드에 싣기로 함. `hnc/ec_data.py`(쌍 분류·MD5 문서 해시 분할 90/5/5), `scripts/build_ec.py` 작성. 커밋 `4a29ab4`.

```bash
pytest -q                                   # 41 passed
tmux new-session -d -s a0c "python scripts/build_ec.py > outputs/a0c/run.log 2>&1 < /dev/null"
```

- 결과: EC 단위 1,699,106개 → 제외 26,747개(빈 문장 25,776, 표식 잔존 971) → **1,672,359쌍** (`datasets/ec/{train,dev,test}.jsonl`, git 제외).

| 분할 | 문서 | 쌍 |
|---|---|---|
| train | 59,294 | 1,517,965 |
| dev | 3,313 | 73,107 |
| test | 3,238 | 81,287 |

| 범주 (전체) | 쌍 | 비율 |
|---|---|---|
| identical (동일) | 323,217 | 19.3% |
| space_only (띄어쓰기만) | 152,019 | 9.1% |
| punct_only (부호만) | 394,628 | 23.6% |
| space_punct (띄어쓰기+부호) | 430,124 | 25.7% |
| content (문자·숫자 내용 변경) | 372,371 | 22.3% |

- 장르: 웹(EX 2021) 222,788쌍 중 동일 55.0%·내용 변경 12.1%, 메신저(MX) 1,449,571쌍 중 내용 변경 23.8%.
- 자판(메신저): 2벌식(쿼티) 985,557쌍(내용 변경 24.5%), 천지인 338,152쌍(22.3%), 그 외(나랏글·단모음·베가·모아키·딩굴·기타) 125,862쌍. 장치는 스마트폰이 약 92%.
- 해석: (1) 부호 편집이 약 절반의 쌍에 걸려 있어, 부호를 채널 범위에 넣을지(§4-2)가 학습량을 크게 좌우한다. (2) 자판 종류가 다르면 오타 분포(자판 인접성)가 다르므로 자모 채널의 자판 prior는 **자판 종류별 혼합**으로 두거나 2벌식 부분집합부터 학습하는 것이 맞다. (3) 내용 변경에는 구어체→표준어 정규화(어미 변이)가 섞여 있어 형태소 채널의 학습 신호가 된다.
- 정정(08:39): `plan.md` A-0c 상태 줄의 자판 비율을 "메신저의 59%·20%"로 잘못 적음(전체 쌍 기준 값). 메신저 쌍 기준 **2벌식 68.0%, 천지인 23.3%**로 고침.

### 08:40–08:55 · A-0d 정렬 파이프라인 구현·표본 측정 [gsm, itcerdo]

- 작업: `hnc/align.py` — (1) 문자 정렬: 공통 접두·접미 제거 후 가중 레벤슈타인(한글 음절 치환 비용 = 0.4 + 0.6 × 정규화 자모 거리, 공백 삽입·삭제 0.5, 공백↔문자 치환 9) (2) 편집 구간을 y의 Kiwi 형태소 위치로 넓혀 형태소에 투영(겹친 형태소 갔=가+었 포함), 공백 편집은 별도 구간 (3) 구간 내부 위치 구분 자모 정렬(같은 키·다른 위치 치환 0.5 → 연음·재음절화 선호). 구간 종류 spacing / punct / edit. 불변식 `rebuild_x(y, regions) == x`. y의 표층과 형태소 위치를 직접 써서 $R(m)$ 없이 정렬(`plan.md` §4-3 (a)). 1차 구현(`c15aedb`) 표본을 직접 훑어 "공백 삭제+철자 편집이 한 구간으로 묶임", "으면→음에서 초성→종성 치환 대신 삭제 경로 선택"을 발견해 보완(`f651093`).

```bash
pytest -q                                                            # 59 passed
python scripts/align_sample.py --n 5000 --inspect 200 --seed 0       # dev 의 content·space_only 표본
```

- 결과 (dev 표본 5,000쌍: content 3,499 · space_only 1,501):
  - 불변식 통과 **5,000/5,000 (100%)**. 정렬 0.21 ms/쌍, Kiwi 분석 0.5 s/5,000문장.
  - 구간 15,093개: spacing 8,054, punct 2,532, edit 4,507. 쌍당 구간 수 1개 28%, 2개 24%, 3개 18%, 4–5개 19%, 6–10개 9%, 10개 초과 1.3%.
  - edit 구간당 형태소 수: 1개 57.3%, 2개 32.2%, 3개 9.1%, 4–5개 1.4%.
  - edit 구간당 자모 편집 수: 1개 57.4%, 2개 22.3%, 3개 10.0%, 4–5개 6.7%, 6개 이상 3.5% → **자모 편집 3개 이하가 89.7%** (설계서의 Brill–Moore 부분열 길이 ≤3 가정과 부합).
  - 검수 표본 200쌍: itcerdo `outputs/a0d/inspect.md`(git 제외). 각 구간에 판정 칸이 있음 → **사용자 육안 검수 대기**(설계서 A-0 완료 판정).

## 2026-11-24 (재접속 후)

### 11:40 · A-1 준비: Pynini 설치 확인 [itcerdo]

- 작업: 설계서가 자모 채널 WFST 도구로 지정한 Pynini/OpenFst를 sudo 없이 Python 3.12 `.venv`에 설치할 수 있는지 확인.

```bash
pip install pynini
python -c "import pynini; a=pynini.accep('ab'); b=pynini.cross('a','x'); print((a @ pynini.cdrewrite(b,'','',pynini.closure(pynini.union('a','b','x')))).string())"
```

- 결과: `pynini 2.1.6.post1` wheel 설치, 재작성 규칙 합성 결과 `xb` 정상. `requirements.txt`에 추가. A-0d 육안 검수가 끝나면 A-1 세부 계획을 `plan.md`에 쓴다.

### 11:50–12:15 · Kor-Learner·Native·Lang8 반입·데이터셋 구축·정렬 통계 [gsm, itcerdo]

- 작업: 사용자 허락(11:49)으로 itcerdo `~/projects/phdq_bart/data/Preprocessed/{korean_learner,native,lang8}`을 `data/kor_gec/`로 복사(원본은 읽기만 함, union은 셋의 합이라 제외). 저자 분할(train/val/test) 그대로 사용. `hnc/kor_gec.py`(탭 쌍·M² 리더, M² 파서는 파일 없이 테스트하도록 줄 목록 입력), `scripts/build_kor_gec.py`(쌍 분류 + KAGAS 편집 결합). 커밋 `a6b496c`. 처음 구축에서 EC용 표식 검사가 학습자 글의 꺾쇠·`&`를 표식으로 잘못 잡아 Kor-Learner 320쌍이 placeholder로 분류됨 → Kor-*에서는 표식 검사를 끄도록 고쳐 재구축.
- 확인: 사람이 붙인 형태소 단위 오류 주석(국립국어원 학습자 말뭉치 XML)은 itcerdo에 없음(`get_data/korean_learner`에는 처리 코드만). M²의 오류유형은 **KAGAS 자동 주석**.

```bash
cp -a ~/projects/phdq_bart/data/Preprocessed/{korean_learner,native,lang8} ~/projects/phdq_prob/data/kor_gec/
pytest -q                                   # 62 passed
python scripts/build_kor_gec.py             # → datasets/kor_gec/<name>_{train,dev,test}.jsonl
for d in korean_learner native lang8; do python scripts/align_sample.py --data datasets/kor_gec/${d}_dev.jsonl --out outputs/a0d_kor/$d --n 5000 --inspect 50; done
```

- 결과 — 데이터 (M² `S` 줄과 원문 일치 100%):

| 자료 | train | dev | test | 분류 (train) |
|---|---:|---:|---:|---|
| Kor-Learner | 19,898 | 4,264 | 4,265 | 내용 변경 99.8% |
| Kor-Native | 12,292 | 2,634 | 2,634 | 띄어쓰기만 45.9%, 내용 변경 54.0% |
| Kor-Lang8 | 76,692 | 16,434 | 16,434 | 내용 변경 96.8%, 띄어쓰기만 2.5% |

- KAGAS 유형(train 편집 수): Kor-Learner — PARTICLE 11,819, UNCLASSIFIED 6,945, ENDING 5,131, SPELL 3,937, CONJUGATION 3,755, NOUN 3,450, INSERTION 2,350 … WS 109. Kor-Native — **WS 10,897**, UNCLASSIFIED 2,709, CONJUGATION 1,391, INSERTION 1,364 …. Kor-Lang8 — UNCLASSIFIED 30,103, PARTICLE 27,326, CONJUGATION 21,771, INSERTION 19,599, DELETION 19,182 ….
- 결과 — 정렬(dev, content·space_only; Lang8은 5,000쌍 표본). 불변식 `rebuild_x == x` 모두 **100%**:

| 자료 | 쌍 | edit / spacing / punct 구간 | edit 구간 형태소 1개 | edit 구간 자모 편집 ≤3 |
|---|---:|---|---:|---:|
| EC (참고, 08:55) | 5,000 | 4,507 / 8,054 / 2,532 | 57.3% | 89.7% |
| Kor-Native | 2,633 | 1,898 / 2,898 / 0 | 76.2% | 89.9% |
| Kor-Learner | 4,264 | 8,834 / 532 / 4 | 74.5% | 74.8% |
| Kor-Lang8 | 5,000 | 10,343 / 2,491 / 207 | 66.2% | 62.9% |

- 해석: 원어민 자료(EC·Native)의 편집은 대부분 자모 3개 이하의 오타·띄어쓰기이고, 학습자 자료(Learner·Lang8)는 형태소 하나를 통째로 바꾸는 대치(조사·어미)가 많아 자모 편집이 커짐. → **자모 채널(작은 부분열 편집)과 형태소 채널(형태소 대치)의 2단 구조가 필요하다는 데이터 근거.** KAGAS 유형은 $z$의 자동 지도 신호로 쓸 수 있으나 사람 주석이 아니므로 $z$ 평가의 금 표준으로는 쓰지 않는다.

### 12:45 · 결정: LM 도구·A-1 착수 시점 [gsm]

- 결정(사용자): (1) n-gram LM 학습은 **사용자가 itcerdo에 sudo로 Boost 개발 패키지를 설치한 뒤 KenLM을 저장소 안에 빌드**해서 쓴다. (2) A-0d 육안 검수 전이라도 A-1을 진행한다(검수 결과로 정렬 규칙이 바뀌면 A-1a를 다시 돌림).
- 사용자 실행 대기 명령: `sudo apt install -y libboost-program-options-dev libboost-system-dev libboost-thread-dev libboost-test-dev libeigen3-dev` (zlib·bz2·lzma 개발 패키지는 이미 있음).

### 12:45–13:00 · A-1a 자모 채널 편집쌍 추출 [gsm, itcerdo]

- 작업: `hnc/channel_edits.py` — A-0d 정렬로 문장 전체 자모 정렬 복원(구간 밖 `=`) → 비일치 덩어리마다 좌우 문맥 0~1개를 붙인 길이 ≤3 부분열 편집 α→β 열거(Brill & Moore 2000), y 쪽 부분열 빈도(분모) 계수. 문맥 없이도 3자모를 넘는 덩어리는 `long`(형태소 채널 몫), 한글 자모·`<SP>` 외 기호가 낀 덩어리는 `other`(A-1 범위 밖)로만 셈. 오류 없는 쌍도 분모에 넣기 위해 모든 범주를 처리. 커밋 `82361a5`, 테스트 67 passed.

```bash
tmux new-session -d -s prob-a1a "python scripts/extract_edits.py --sources ec2,native --tag ec2_native --workers 12 > outputs/a1/extract.log"
```

- 결과 (22초): EC train 2벌식 + Kor-Native train **911,836쌍**(편집 있는 쌍 782,446). 덩어리: 범위 안 892,672 / 범위 밖 기호 853,654 / 3자모 초과 7,388(범위 안 덩어리의 0.8%). 편집 종류 41,462(문맥 확장 포함), 핵심 편집 종류 5,653. 산출 `datasets/channel/ec2_native/`(git 제외).
- 핵심 편집 상위(계수, 비율 = 계수 / y 쪽 α 빈도):

| α → β | 계수 | 비율 | 현상 |
|---|---:|---:|---|
| `<SP>` → ∅ | 665,224 | 0.241 | 띄어쓰기 생략 |
| ∅ → `<SP>` | 31,225 | 0.001 | 띄어쓰기 추가 |
| ᆻ → ᆺ | 26,987 | 0.105 | 쌍받침 Shift 누락(했→햇) |
| ᅩ → ᅮ | 17,666 | 0.017 | 구어체 모음(하려고→하려구) |
| ᅨ → ᅦ | 3,779 | 0.126 | ㅖ/ㅔ 혼동 |
| ᅭ → ᅧ | 3,599 | 0.012 | 구어체(요→여) |
| `<SP>` ᄀ → ᄁ | 3,265 | 0.008 | 띄어쓰기 + 된소리(할 게→할께) |
| ᆫ ᇂ ᄋ → ᄂ | 2,892 | 0.073 | 소리 나는 대로(않아→아나) |
| ᆽ ᄋ → ᄌ | 2,114 | 0.075 | 연음 표기(맞아→마자) |
| ᅢ → ᅵ / ᅦ → ᅢ / ᅢ → ᅦ | 1,803 / 2,076 / 1,225 | — | 돼/되, ㅐ/ㅔ 혼동 |

- 해석: 설계서 §1.2(b)가 예상한 현상(모음 혼동, 받침, 연음, 띄어쓰기, 쌍자음)이 상위 편집에 그대로 나타남. 특히 **연음·소리 나는 대로 표기가 위치 구분 표상에서 단일 부분열 편집으로 잡힘**(초·종성 구분 결정의 실측 근거). 3자모 초과 덩어리가 0.8%뿐이라 원어민 자료에서는 ≤3 창이 충분.

### 13:00–13:35 · A-1b 자모 채널 추정·WFST·dev 우도 [gsm, itcerdo]

- 작업: `hnc/jamo_channel.py` — 다자모 편집은 상대빈도(2회 이상), 단일 자모 대치·삭제·삽입은 디리클레 평활(κ=1, 출력 공간 균등 기저)로 전부 양의 확률, 동일 편집 = 1 − 오류 질량, 범위 밖 기호는 동일 비용 0·삭제/삽입 log 1e-9(전 지지). Pynini `string_map(...).closure()`로 $T_J$, 점수 = `y ∘ T_J ∘ x`의 열대 최단 거리. Pynini는 가중치를 문자열로 받음(시험 중 확인).
  - 1차 적합 실패: `string_map` 컴파일 오류 → 이분 탐색으로 원인 행 `\n <SP> → \n` 발견. A-1a가 핵심 덩어리만 범위 검사하고 **문맥 확장은 검사하지 않아** 줄바꿈·부호가 문맥으로 섞임 → 문맥까지 범위 검사하도록 수정(`8587617`), A-1a 재추출(편집 종류 41,462 → 35,936, 핵심 편집 5,653 그대로).
- 테스트 71 passed.

```bash
python scripts/extract_edits.py --sources ec2,native --tag ec2_native --workers 12      # 재추출 21.7 s
tmux new-session -d -s prob-a1b "python scripts/fit_channel.py --tag ec2_native --n 3000 --workers 12 > outputs/a1/fit.log"
```

- 결과: 편집 21,227개, 범위 밖 기호 2,387개, FST 행 28,417·상태 44,492(구성 0.1 s, 추정 31 s). dev 평가 3,000쌍씩(Native는 2,634 전체), **유한 비율 100%** (8,634/8,634) → 완료 기준 충족. D = −log P_J(x|y_정답) − (−log P_J(x|x)) = 정답 교정의 채널 비용(사전확률이 넘어서야 하는 값):

| 집합 / 범주 | 쌍 | 쌍당 NLL | 자모당 NLL | D 중앙값 | D 90% |
|---|---:|---:|---:|---:|---:|
| EC2 동일 | 408 | 0.41 | 0.025 | 0 | 0 |
| EC2 띄어쓰기만 | 312 | 2.77 | 0.098 | 1.43 | 3.72 |
| EC2 부호만 | 739 | 28.73 | 1.463 | 20.72 | 41.45 |
| EC2 띄어쓰기+부호 | 814 | 34.11 | 1.022 | 22.94 | 62.72 |
| EC2 내용 변경 | 727 | 42.95 | 1.303 | 30.38 | 78.81 |
| Native 띄어쓰기만 | 1,168 | 5.15 | 0.073 | 1.43 | 5.91 |
| Native 내용 변경 | 1,465 | 17.14 | 0.261 | 8.35 | 35.61 |
| Learner 내용 변경 | 2,996 | 38.14 | 0.555 | 24.68 | 77.76 |

- 해석: (1) 띄어쓰기 교정의 채널 비용은 작음(중앙값 1.4 nats) → 사전확률이 쉽게 넘어섬. (2) 부호 편집 하나 = 범위 밖 벌점 20.72(= −log 1e-9) — 의도대로 A-1 범위 밖. EC 내용 변경 쌍의 D가 큰 것도 대부분 같은 쌍의 부호 편집 때문. (3) 학습자 오류는 자모 채널만으로 비용이 큼(D 중앙값 24.7) → 형태소 채널 필요. (4) 속도: 쌍당 평균 0.06–0.33 s, 최대 5 s → 디코딩(A-1d) 전에 편집 구간 한정 계산·FST 최적화 필요.

## 2026-11-24 (오후)

### 18:43–18:55 · Boost 설치 후 커널·NVIDIA DKMS 오류 진단 [itcerdo, 조회만]

- 상황: 사용자가 `sudo apt install libboost-…-dev libeigen3-dev` 실행 → 대기 중이던 커널 6.8.0-149 구성 단계에서 NVIDIA 570.124.04 DKMS 빌드 실패(`make … KERNEL_UNAME=6.8.0-149-generic modules` 종료 코드 2), dpkg 오류.
- 조회(시스템을 바꾸지 않음): `uname -r`, `nvidia-smi`, `dkms status`, `dpkg -l`, `apt-mark showhold`, `apt-cache policy nvidia-driver-*`. DKMS 빌드 로그(`/var/lib/dkms/...`)는 지정 디렉토리 밖이라 읽지 않음.
- 결과:
  - 실행 중 커널 **6.8.0-139**, GPU RTX 5090·드라이버 570.124.04 정상, DKMS `nvidia/570.124.04, 6.8.0-139: installed`. 다른 프로젝트 tmux 13개 영향 없음.
  - **Boost(program-options·system·thread·test 1.83)·Eigen 개발 패키지는 설치 완료(ii).**
  - 6.8.0-149: `linux-image`·`linux-headers-…-generic` 구성 실패(iF), `linux-generic`·`linux-headers-generic` 미구성(iU). 6.8.0-142·146·147 이미지가 이미 제거(rc)된 이력 → 같은 문제로 새 커널을 지워 온 것으로 보임.
  - 설치된 NVIDIA Ubuntu 패키지 없음(.run 설치본으로 추정). 저장소 후보: `nvidia-driver-570(-open)` 570.211.01, `nvidia-driver-580-open` 580.196.07.
  - 위험: 이대로 재부팅하면 6.8.0-149로 부팅될 수 있고 그 커널에는 NVIDIA 모듈이 없음 → GPU 사용 불가.

### 18:45–18:50 · KenLM 빌드·시험 [gsm, itcerdo]

- 작업: `tools/`를 git 제외로 추가. `tools/kenlm`에 KenLM 소스(GitHub `kpu/kenlm`) 받아 Release 빌드(tmux `prob-kenlm`, 컴파일러 임시 파일은 `TMPDIR=.cache/tmp`). `pip install kenlm`(질의 모듈), `requirements.txt`에 추가.

```bash
git clone https://github.com/kpu/kenlm.git tools/kenlm && cd tools/kenlm/build && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j 16 lmplz build_binary query
tools/kenlm/build/bin/lmplz -o 3 -S 2G -T .cache/tmp --discount_fallback < outputs/kenlm/smoke.txt > outputs/kenlm/smoke.arpa   # EC train 교정문 2만 줄
tools/kenlm/build/bin/build_binary outputs/kenlm/smoke.arpa outputs/kenlm/smoke.bin
python -c "import kenlm; m=kenlm.Model('outputs/kenlm/smoke.bin'); print(m.score('오늘 날씨 좋네요 .'), m.score('좋네요 날씨 오늘 .'))"
```

- 결과: `lmplz`·`build_binary`·`query` 빌드 성공(약 2분), 시험 3-gram 학습·이진화·Python 질의 정상(정상 어순 −14.83 > 뒤섞은 어순 −15.99). 임시 파일은 저장소 안에만 생기고 정리됨. → A-1d·A-2 진행 가능.
