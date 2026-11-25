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

### 18:51–18:55 · DKMS 실패 원인 확정, 장기 대응 정정 [itcerdo, 조회만]

- 사용자가 `sudo tail -n 40 /var/lib/dkms/nvidia/570.124.04/build/make.log` 결과를 공유. 실패 지점: `nvidia/nv-pci.c:240` — 드라이버가 `pci_resize_resource(pci_dev, NV_GPU_BAR1, requested_size)`를 인자 3개로 호출하나, 6.8.0-149 헤더(`include/linux/pci.h:1445`)의 선언은 인자가 하나 더 있음(`too few arguments`). → Ubuntu 6.8 커널 갱신에 들어온 커널 API 변경을 570.124.04 소스가 따라가지 못함. `nv_encode_caching` 메시지는 경고로 실패 원인 아님. DKMS 재빌드로는 해결 불가, 드라이버 교체 필요.
- 조회: `apt-cache policy linux-modules-nvidia-*-6.8.0-149-generic` → 6.8.0-149용 사전 빌드 모듈은 **580-open만 있음**(`linux-modules-nvidia-580-open-6.8.0-149-generic` 6.8.0-149.149). 570-open은 이 커널용 사전 빌드본이 없음.
- 정정: 장기 대응은 `.run` 드라이버 제거 후 **`nvidia-driver-580-open` + 사전 빌드 커널 모듈**로 교체(재부팅 필요, GPU 작업이 없을 때). 즉시 조치(6.8.0-149 제거, 139 유지)는 그대로.

### 18:53–19:05 · 커널 정리 확인, A-2 형태소 LM 착수(v0), 신문 말뭉치 전송 [gsm, itcerdo]

- 확인(조회만): 사용자가 6.8.0-149 정리 완료 → 실행·설치 커널 6.8.0-139만, 미구성 패키지 없음, GPU 정상. 남은 149 관련은 `linux-libc-dev`·`linux-tools-*`(정상).
- 작업: `hnc/morph_lm.py`(LM 토큰 `▁형태/품사` — PCFG 말단과 같은 어절 경계 표시, KenLM 래퍼, 자연로그), `hnc/corrupt.py`(K5와 같은 오류 주입 4종), `scripts/build_lm_corpus.py`(EC·Kor-* train 교정문 + 신문 문단을 Kiwi로 문장 분리·분석), `scripts/train_lm.sh`(lmplz 5-gram, 3-gram 이상 단독 n-gram 가지치기, 임시 파일은 저장소 안), `scripts/eval_lm.py`(dev 퍼플렉시티·미등록 비율 + 같은 오류 문장에 대한 LM·PCFG·합의 판별력; PCFG는 `~/projects/phdq_pcfg2`의 `PCFGScorer`). 테스트 73 passed.
- 신문 말뭉치 2024·2025(48개 파일, 7.3GB) gsm → itcerdo `data/` 전송(8분), 파일별 바이트 수 일치. 디스크 여유 58GB.

```bash
python scripts/build_lm_corpus.py --tag v0 --no-news --workers 16    # 24 s
bash scripts/train_lm.sh v0 5                                       # lm5.bin 62 MB
python scripts/eval_lm.py --tag v0 --order 5 --n 2000 --workers 12
```

- 결과 v0 (신문 없음; EC train 1,516,808 + Kor-* train 108,882문장, 1,450만 토큰):
  - dev 퍼플렉시티(미등록 비율): EC2 51.7 (0.37%), Kor-Learner 41.2 (0.25%), Kor-Lang8 55.7 (0.35%), Kor-Native 22.7 (0.26%).
  - 판별력(원문 > 오류 문장, dev 각 2,000문장 표본):

| 집합 / 오류 | n | LM 원점수 | LM 형태소당 | PCFG 원점수 | PCFG 형태소당 | 합 원점수 | 합 형태소당 |
|---|---:|---:|---:|---:|---:|---:|---:|
| EC2 격조사 대치 | 331 | **0.918** | 0.918 | 0.628 | 0.628 | 0.909 | 0.909 |
| EC2 어절 순서 | 1,645 | **0.942** | 0.942 | 0.713 | 0.713 | 0.928 | 0.928 |
| EC2 종결→연결어미 | 1,112 | **0.892** | 0.892 | 0.662 | 0.662 | 0.835 | 0.835 |
| EC2 조사 삭제 | 946 | 0.528 | 0.936 | 0.189 | 0.932 | 0.385 | **0.963** |
| Learner 격조사 대치 | 1,530 | **0.936** | 0.936 | 0.586 | 0.586 | 0.926 | 0.926 |
| Learner 어절 순서 | 1,997 | 0.946 | 0.946 | 0.776 | 0.775 | **0.953** | **0.953** |
| Learner 종결→연결어미 | 1,976 | 0.956 | 0.956 | 0.953 | 0.953 | **0.961** | **0.961** |
| Learner 조사 삭제 | 1,959 | 0.674 | 0.956 | 0.365 | 0.966 | 0.581 | **0.981** |
| Lang8 격조사 대치 | 1,318 | **0.910** | 0.910 | 0.580 | 0.580 | 0.904 | 0.904 |
| Lang8 어절 순서 | 1,995 | 0.942 | 0.942 | 0.755 | 0.754 | **0.947** | **0.947** |
| Lang8 종결→연결어미 | 1,797 | **0.910** | 0.910 | 0.831 | 0.831 | 0.875 | 0.875 |
| Lang8 조사 삭제 | 1,851 | 0.615 | 0.944 | 0.332 | 0.957 | 0.515 | **0.969** |
| Native 격조사 대치 | 1,616 | **0.960** | 0.960 | 0.647 | 0.647 | 0.957 | 0.957 |
| Native 어절 순서 | 2,000 | 0.983 | 0.983 | 0.780 | 0.780 | **0.987** | **0.987** |
| Native 종결→연결어미 | 1,983 | **0.948** | 0.948 | 0.869 | 0.869 | 0.930 | 0.930 |
| Native 조사 삭제 | 1,963 | 0.777 | 0.962 | 0.338 | 0.935 | 0.713 | **0.974** |

- 해석: (1) **형태소 LM이 모든 오류 종류에서 PCFG보다 강함** — 특히 격조사 선택(LM 0.91–0.96 vs PCFG 0.58–0.65), 설계서의 역할 분담과 부합. (2) PCFG는 조사 삭제(형태소당)와 어순에서 **LM에 더해 주는 몫이 있음**(합 > LM 단독, 네 집합 모두). (3) 같은 가중치의 단순 합은 종결어미·격조사에서 LM 단독보다 나빠짐 → PCFG 점수 규모가 커서 지배함. **λ 조정 필요**(설계서 B-5 "PCFG 한계기여"의 근거). (4) 조사 삭제는 원점수 비교가 길이에 편향됨(K5와 같은 결과) → 길이 보정 필수.

### 19:05–19:45 · A-2 형태소 LM v1 (신문 포함) [gsm, itcerdo]

- 작업: 신문 말뭉치 포함 학습 자료 구축 → 5-gram 학습 → 평가(tmux `prob-a2v1`). 자료가 예상보다 커서(11.2억 토큰) ARPA가 공용 디스크를 압박할 것을 우려해, 학습 시작 전에 `train_lm.sh` 기본 가지치기를 `0 1 2`(2-gram 단독 제거, 3-gram 이상 2회 이하 제거)로 강화하고 이진 파일을 8비트 양자화하도록 바꿈(`907807e`, **v0은 `0 0 1`·양자화 없음**). 학습 중 디스크 여유를 감시(가장 낮을 때 26 GB; lmplz 임시 파일은 이름 없이 열려 있어 `du`가 아니라 `df`에만 보임).

```bash
python scripts/build_lm_corpus.py --tag v1 --workers 16      # 1,430 s
bash scripts/train_lm.sh v1 5                                # lmplz + build_binary trie -q 8 -b 8
python scripts/eval_lm.py --tag v1 --order 5 --n 2000 --workers 12
```

- 결과: 신문 20,745,918문단 → 30,009,162문장·1,101,374,422토큰 + EC·Kor-* 교정문 14.5M 토큰 = 약 **11.2억 토큰**(train.txt 11.7 GB). ARPA 9.7 GB → 이진 1.14 GB(ARPA 삭제).
  - dev 퍼플렉시티(미등록 비율), v0 → v1: EC2 51.7 → **102.2** (0.37% → 0.20%), Learner 41.2 → 51.2, Lang8 55.7 → 80.8, Native 22.7 → 39.3 (0.26% → 0.02%).
  - 판별력(LM 원점수, 조사 삭제는 형태소당), v0 → v1:

| 집합 | 격조사 대치 | 어절 순서 | 종결→연결어미 | 조사 삭제(형태소당) | 합(LM+PCFG) 조사 삭제(형태소당) |
|---|---|---|---|---|---|
| EC2 | 0.918 → **0.925** | 0.942 → 0.934 | 0.892 → **0.905** | 0.936 → **0.952** | 0.963 → **0.970** |
| Learner | 0.936 → **0.944** | 0.946 → **0.954** | 0.956 → **0.964** | 0.956 → **0.967** | 0.981 → **0.985** |
| Lang8 | 0.910 → **0.929** | 0.942 → **0.950** | 0.910 → **0.922** | 0.944 → **0.954** | 0.969 → **0.976** |
| Native | 0.960 → **0.967** | 0.983 → 0.982 | 0.948 → 0.933 | 0.962 → **0.968** | 0.974 → **0.976** |

- 해석: (1) 퍼플렉시티는 나빠짐 — 학습 자료의 99%가 신문이라 메신저·학습자 문체와 영역 불일치. (2) 판별력은 16개 중 13개 항목에서 좋아짐 — 큰 말뭉치의 문법 지식이 일반화. (3) 영역 보정의 표준 해법은 **영역 내 LM(v0)과 신문 LM(v1)의 토큰 단위 선형 보간**(가중치는 dev 퍼플렉시티로) → 다음 개선 항목. (4) PCFG를 같은 가중치로 더하면 조사 삭제(형태소당)는 계속 좋아지고 종결어미·격조사는 나빠지는 양상이 v0과 같음 → λ 조정 필요.

### 19:50–22:10 · A-1d 디코더·A-1e 평가 (Kor-Native dev) [gsm, itcerdo]

- 결정(사용자, 19:50): 디코더는 **어절 후보 생성(채널 편집 역적용) + 형태소 LM 빔 탐색**(설계서 §2.1의 n-gram WFST 합성 대신 — 형태소 LM이 후보의 형태소 분석을 요구), 평가 도구는 `phdq_bart`의 m2scorer·KAGAS를 `tools/`로 복사.
- 평가 도구: `~/projects/phdq_bart/Standard_Korean_GEC/{metric/m2scorer, KAGAS}`를 `tools/`로 복사(git 제외). Native dev에서 정답을 가설로 내면 F0.5 1.0, 원문은 0.0(채점 1.7 s). 채점은 m2scorer + 데이터의 공식 M²만 필요(KAGAS는 정답 M²가 없는 EC용).
- 어휘(`scripts/build_vocab.py`, 243 s): EC·Kor-* train 교정문 + 신문의 어절 핵심(앞뒤 부호 제거) 빈도 — 15,004,745종, 5회 이상 2,661,772종(신문 4.8억 + 교정문 700만 어절).
- 디코더(`hnc/decoder.py`): 확률 ≥1e-3(띄어쓰기 편집은 ≥1e-5) 채널 편집을 역적용(1단계 → 근사 비용 상위 40개에 2단계), 유효한 자모열이고 모든 어절이 어휘에 있는 후보 상위 8개를 채널 WFST로 정확히 채점 + 원문 후보. 구간 = x 어절 1개(철자·분리) 또는 2개(합치기). 빔 탐색 점수 = λ_LM·log P_LM − 채널 비용(구간 사이 공백 동일 비용 포함) + λ_len·형태소 수. 후보 준비(`prepare`)와 탐색(`search`)을 나눠 λ 격자를 싸게 시험. 테스트 3개(철자·분리·합치기 교정, λ_LM=0이면 원문 유지) — 계수가 비현실적이면(평활 백오프가 큼) 후보 상위가 백오프 편집으로 채워지는 문제를 테스트 작성 중 확인, 시험 계수를 현실화.
- 평가(`scripts/decode.py`): m2scorer로 (a) 정답 M² 전체 (b) A-1 범위(KAGAS WS·SPELL)만 남긴 정답 — (b)에서 범위 밖 편집을 고치면 오탐.

```bash
python scripts/decode.py --set native --split dev --n 100 --workers 12 --tag try100          # 시험
tmux new-session -d -s prob-a1d "python scripts/decode.py --set native --split dev --workers 12 --lam-lm 0.5,1,1.5,2,3 --lam-len 0,1,2 --tag native_v1"
```

- 결과 (Kor-Native dev 2,634문장, LM v1, 채널 ec2_native, 빔 8, 후보 8; 694 s, 문장당 평균 3.1 s·최대 8.9 s):

| λ_LM | λ_len | 바뀐 문장 | 전체 P / R / **F0.5** | WS+SPELL P / R / **F0.5** |
|---:|---:|---:|---|---|
| 0.5 | 0 | 1,575 | .799 / .382 / .656 | .659 / .539 / **.631** |
| 1.0 | 0 | 1,760 | .775 / .432 / .669 | .637 / .602 / .630 |
| 1.5 | 0 | 1,818 | .764 / .451 / **.671** | .626 / .624 / .626 |
| 2.0 | 0 | 1,840 | .760 / .457 / **.671** | .623 / .634 / .625 |
| 3.0 | 0 | 1,881 | .747 / .464 / .666 | .613 / .643 / .619 |
| 1.5 | 1 / 2 | 1,845 / 1,868 | — / — / .663 / .656 | — / — / .617 / .610 |

  (λ_len > 0은 모든 λ_LM에서 F0.5를 낮춤.)
- 해석: (1) **A-1 완료 기준 충족** — 맞춤법·띄어쓰기 유형 F0.5 측정(0.631), 전체 F0.5 0.671. (2) 참고: 같은 데이터의 **test**에서 논문 방식 KoBART F0.5 74.7, 표준 학습 seq2seq 86–89(phdq_bart2) — 집합(dev/test)·조정 방식이 달라 직접 비교는 아니나, 설계서의 예상("신경 기준선보다 열세")과 부합. (3) λ는 dev에서 고른 값이라 낙관적 → 최종 비교는 test에서 한 번만. (4) 속도(문장당 3 s)는 후보 생성(Python 열거)과 채널 정확 채점이 대부분 → 개선 필요.

### 22:11–22:35 · 검수 파일 로컬 전달, 디코더 속도 개선 [gsm, itcerdo]

- 검수 파일을 gsm으로 복사(사용자 요청): `phdq_prob/outputs/a0d/inspect.md`(EC 200쌍), `outputs/a0d_kor/inspect_{korean_learner,native,lang8}.md`(각 50쌍). `outputs/`는 git 제외(말뭉치 문장 포함).
- 프로파일(30문장, 단일 프로세스 80.7 s): 역편집 열거 47%, 채널 WFST 정확 채점 21%(호출당 9 ms), 상위 선택·사전 갱신 약 20%, Kiwi 0.6%.
- 개선 ①(`93e2697`): 이미 편집된 자모는 다시 편집하지 않음(겹치지 않는 분할), 후보 비용을 **분할 비용**(바뀌지 않은 자모의 동일 비용 + 편집 비용; WFST 최단 거리의 상한)으로 직접 계산해 디코딩 중 WFST 호출 제거(`exact_channel` 옵션으로 유지), 2단계 확장 상위 40 → 20. → 30문장 42.5 s.
- 개선 ②(`be9e1b4`): 문맥 없는 순수 삽입(β = ∅) 101개를 모든 경계에 넣던 것을 비용 상위 8개로 제한(`<SP>`·`<SP><SP>` 등; 같은 패턴의 문맥 붙은 형태는 제자리에서만 적용됨). → 100문장 문장당 0.57 s.

```bash
python scripts/decode.py --set native --split dev --workers 12 --lam-lm 0.5,1,1.5,2,3 --lam-len 0 --tag native_v1_fast
```

- 결과 (Kor-Native dev 2,634문장): **92 s(이전 694 s, 7.5배)**, 문장당 평균 0.42 s·최대 1.8 s.

| λ_LM | 바뀐 문장 | 전체 P / R / F0.5 (이전) | WS+SPELL F0.5 (이전) |
|---:|---:|---|---|
| 0.5 | 1,667 | .796 / .411 / .670 (.656) | **.621** (.631) |
| 1.0 | 1,864 | .767 / .465 / .679 (.669) | .618 (.630) |
| 1.5 | 1,933 | .754 / .485 / **.679** (.671) | .615 (.626) |
| 2.0 | 1,963 | .747 / .493 / .677 (.671) | .612 (.625) |
| 3.0 | 2,008 | .733 / .501 / .671 (.666) | .605 (.619) |

- 해석: 전체 F0.5 최고 0.671 → **0.679**, WS+SPELL 최고 0.631 → 0.621(고친 문장이 늘며 범위 밖 교정이 오탐으로 잡힘). 이후 실험은 빠른 디코더로 한다.

### 22:35–23:00 · A-1c 채널 MAP-EM [gsm, itcerdo]

- 작업: A-1a에 덩어리 창 추출 추가(`atom_windows`: 범위 안·3자모 이내 덩어리 ±1 문맥, 범위 밖 문맥은 잘라냄) → 학습 창 892,672개(24,805종), dev(EC2 dev·Native dev) 창 49,734개(4,364종). `hnc/channel_em.py` — 편집쌍 **결합 분포** δ(α, β)의 MAP-EM(Ristad & Yianilos를 |α|,|β| ≤ 3 부분열로 확장; E단계 창별 (i, j) 격자 전·후진, M단계 δ = (기대 계수 + λ₀)/(합 + λ₀·|목록|), λ₀ = 0.01), 창 밖의 바뀌지 않은 자모는 길이 1 동일쌍으로 고정 계수. 초기값 = Brill–Moore 계수 + 단일 동일쌍 + 단일 대치·삭제·삽입 백오프(0.01). 디코더용 조건부 확률은 EM 기대 계수를 A-1b와 같이 부분열 빈도로 정규화. 테스트 3개(손 계산 대조, MAP 목적함수 단조, 변환).

```bash
python scripts/em_channel.py --tag ec2_native --dev dev_ec2_native --iterations 5        # 4.7 s
python scripts/decode.py --set native --split dev --workers 12 --lam-lm 0.5,1,1.5,2,3 --lam-len 0 --channel datasets/channel/ec2_native_em --tag native_v1_em
```

- 결과 — EM(목록 37,903쌍): 학습 목적함수 −80,195,717 → −78,810,180 → … → **−78,690,398 (단조 증가)**, 도달 불가 창 0. **dev 창당 로그우도는 −9.00(초기) → −9.74 → −9.64로 나빠짐.** 기대 계수 상위가 `ᆫ <SP> ᄀ → ᆫ ᄀ`(77,888), `<SP> ᄋ → ᄋ`(22,137)처럼 **문맥까지 묶은 긴 조각**으로 쏠림(문맥 없는 `<SP> → ∅`은 28,029) — 결합 분할 모형은 조각 수가 적을수록 곱이 커서 긴 조각을 선호하는 알려진 퇴화 → 학습에 없던 문맥으로의 일반화 약화.
- 결과 — 디코딩(Kor-Native dev 2,634문장, 편집 21,227 → 13,578개, 55 s·문장당 0.25 s):

| λ_LM | 바뀐 문장 | 전체 P / R / F0.5 (EM 전) | WS+SPELL F0.5 (EM 전) |
|---:|---:|---|---|
| 0.5 | 1,652 | .825 / .423 / **.693** (.670) | .598 (**.621**) |
| 1.0 | 1,931 | .771 / .486 / .690 (.679) | .586 (.618) |
| 1.5 | 2,033 | .751 / .515 / .688 (.679) | .581 (.615) |
| 2.0 | 2,087 | .735 / .526 / .681 (.677) | .575 (.612) |
| 3.0 | 2,146 | .714 / .535 / .669 (.671) | .565 (.605) |

- 해석: (1) 전체 F0.5 최고 0.679 → **0.693**(+1.4, 정밀도 .825) — EM이 문맥 의존 편집(구어체 어미 등)을 강화해 정답의 다른 유형 교정이 늘어남. (2) WS+SPELL 기준은 0.621 → 0.598로 하락(그 범위 밖 교정이 오탐으로 집계). (3) held-out 우도 하락과 교정 성능 향상이 엇갈림 → 긴 조각 퇴화를 다스리는 방법(조각 길이 사전분포, 단일 자모 EM + 문맥 편집 분리, 설계서 A-6의 희소 디리클레 VB)은 후속 과제. 우선 전체 F0.5 기준으로 EM 채널(`ec2_native_em`)을 기본으로 쓴다.

### 23:00–23:25 · EC dev 정답 M² 생성(KAGAS)·평가 [gsm, itcerdo]

- 작업: KAGAS 전용 환경 `.venv-kagas`(저장소 안, git 제외; `hunspell spacy tqdm konlpy soylemma soynlp jamo`, 시스템 Java 21·libhunspell-dev 사용) — 저자 샘플 정상(WS·INSERTION 유형). `scripts/make_ec_gold.py`: EC dev 2벌식 무작위 5,000쌍(범주 무관, 공백 정규화) → KAGAS → `datasets/ec_gold/ec2_dev5k/gold.m2`. `decode.py`에 `--records/--gold-m2`.
  - 실패 1: venv python 경로에 `resolve()`를 써서 심볼릭 링크가 시스템 python으로 풀림 → `ModuleNotFoundError: spacy` → `absolute()`로 수정.
  - 실패 2: KAGAS의 Kkma(KoNLPy/JPype)가 BMP 밖 문자(이모지)에서 `UnicodeDecodeError`로 1,000문장 뒤 중단 → BMP 밖 문자가 든 쌍을 표본에서 제외(5쌍).
- 결과: 정답 M² 5,000블록(KAGAS 53 s). 범주: 내용 1,178 / 띄어쓰기+부호 1,430 / 부호 1,168 / 띄어쓰기 488 / 동일 736. 정답 편집: INSERTION 2,866(주로 문장 끝 부호), PUNCT 2,316, WS 1,692, UNCLASSIFIED 1,402, CONJUGATION 873, noop 760, NOUN 242, SPELL 140 … → **편집의 절반 이상이 부호(A-1 범위 밖).**
- 디코딩(문장당 0.08–0.14 s):

| 채널 | λ_LM | 바뀐 문장 | 전체 P / R / F0.5 | WS+SPELL P / R / F0.5 |
|---|---:|---:|---|---|
| Brill–Moore | 0.5 | 1,895 | .649 / .229 / .475 | .479 / .608 / **.500** |
| Brill–Moore | 2.0 | 2,344 | .616 / .282 / **.498** | .436 / .716 / .473 |
| EM | 0.5 | 1,667 | .655 / .194 / .444 | .494 / .542 / **.503** |
| EM | 1.5 | 2,252 | .613 / .265 / **.485** | .436 / .688 / .471 |

- 해석: (1) 전체 재현율이 낮은 것은 부호 편집이 범위 밖이기 때문. (2) **EC에서는 Brill–Moore 채널이 EM보다 전체 F0.5가 약간 높음**(.498 vs .485) — Native(EM .693 > BM .679)와 반대, held-out 우도 하락과 부합 → EM 채널을 일괄 기본으로 쓰는 결정은 보류, 자료별로 비교해 보고.

### 23:25–23:40 · B안식 PCFG 재채점, n-best 오라클(B-0), PCFG 한계기여(B-5) [gsm, itcerdo]

- 작업: 디코더에 n-best(최종 빔, 서로 다른 출력, 성분 점수 LM·채널·형태소 수) 추가(`search_nbest`, 테스트 1개 추가). `scripts/rescore.py` — n-best(8)의 각 가설을 Kiwi로 분석해 `phdq_pcfg2`의 `PCFGScorer`(K5)로 비터비 로그확률을 붙이고, 점수 = λ_LM·LM − 채널 + λ_P·PCFG로 다시 골라 m2scorer 채점. 오라클 = 문장마다 정답 교정문과 문자 유사도가 가장 높은 가설. `--from-nbest`로 저장된 n-best에서 격자만 다시.

```bash
python scripts/rescore.py --set native --split dev --channel datasets/channel/ec2_native_em --base-lam-lm 1.0 --nbest 8 --tag native_em
python scripts/rescore.py --records datasets/ec_gold/ec2_dev5k/records.jsonl --gold-m2 datasets/ec_gold/ec2_dev5k/gold.m2 --channel datasets/channel/ec2_native --base-lam-lm 1.0 --nbest 8 --lam-lm 1,1.5,2,3 --tag ec2_dev5k_bm
python scripts/rescore.py ... --from-nbest --lam-pcfg 0.3,0.5,0.75,1,1.5,2     # 격자 확장
```

- 결과: n-best 생성·PCFG 채점 Native 2,634문장 45 s(가설 평균 8.0, PCFG 정상 파스 99.0%), EC 5,000문장 41 s(가설 7.1, 정상 파스 94.5%).

| 집합 (채널) | n-best 오라클 F0.5 | 재채점 PCFG 없음 (λ_P=0) | 최고 (λ_LM, λ_P) | **PCFG 한계기여** |
|---|---:|---:|---|---:|
| Kor-Native dev (EM) | **0.810** (P .869 R .635) | 0.692 (λ_LM 0.5) | **0.717** (0.5, 0.5; P .830 R .465) | **+2.5** |
| EC dev 5k (BM) | 0.558 (P .660 R .345) | 0.498 (λ_LM 2) | 0.502 (2, 0.2–0.3) | +0.3 |

  - Native, λ_LM = 0.5에서 λ_P: 0 → .692, 0.1 → .704, 0.3 → .713, **0.5 → .717**, 0.75 → .715, 1 → .709, 2 → .693.
  - WS+SPELL F0.5도 Native에서 .598 → .600–.602로 소폭 상승.
- 해석: (1) **B-0 관문 통과** — n-best 오라클이 1-best보다 훨씬 높아(Native .81 vs .69) 재순위화 여지가 큼. (2) **강한 형태소 LM 위에서도 PCFG가 실제 이득**(Native +2.5점) — 설계서 B-5 질문의 첫 실측. 메신저 구어체(EC)에서는 이득이 작음(+0.3) — PCFG가 신문·구어 DP로 학습됐고 EC 오류의 다수가 띄어쓰기·부호라 구조 판단의 몫이 작음. (3) λ는 dev에서 고른 값(낙관적) → test에서 한 번만 최종 비교.

### 11:29–11:45 · A-0d 검수 파일 v2로 교체 [gsm, itcerdo]

- 경위: 사용자가 v1 `outputs/a0d/inspect.md`를 9쌍까지 판정하다 "거의 모두 y가 정문, x가 비문 — 형편없다, 무슨 기준으로 골랐나"고 지적. 원인은 **표기**: v1은 구간을 잡음 채널 방향(`y → x`, 정답 → 오류)으로만 적고 x·y가 무엇인지(x = 사람이 친 원문 `form`, y = 교정자 정답 `corrected_form`) 설명하지 않아, y를 원문·x를 고친 결과로 읽게 됨. 사용자의 bad는 "교정이 틀렸다"는 판정이었고 정렬 판정이 아님. 표본 선정은 품질 선별 없이 무작위(EC dev에서 content·space_only 쌍 5,000개 저수지 표집, seed 0 → 그중 200쌍 무작위, seed 1).
- v1 판정에서 얻은 것: 5번 `엔젤`(원문) ⇒ `엔절`(교정)처럼 **교정 정답 자체가 의심스러운 경우**가 있음(외래어 표기 `에인절`도 아님). 6·7번(`남주상`, `name2님`)은 원문 쪽이 낫다는 판정 — 교정자는 `님`(이름 뒤 의존명사)을 띄는 규범을 따름. → EC 정답에 규범 선택·오류가 섞여 있음.
- 작업: `scripts/align_sample.py` 검수 출력 변경(`e1f7a1c`) — (1) 머리말에 읽는 법(x·y 정의, 채널 방향이면 y 정문·x 비문이 정상, 검수 대상은 정렬이지 교정의 옳고 그름이 아님), (2) 구간을 교정 방향 `원문 ⇒ 교정`으로, 자모 편집은 `[교정→원문]`(∅ = 없음)으로, (3) 판정 대상을 내용 편집 구간 `[E#]`로 한정(띄어쓰기·부호는 개수만), (4) 내용 편집이 있는 쌍에서만 표집, 100쌍. 정답 오류 표시 `gold?` 추가.

```bash
python scripts/align_sample.py --n 5000 --inspect 100 --seed 0     # itcerdo, 2 s
```

- 결과: 표집 모집단 3,499쌍, 검수 100쌍 / 편집 구간 136개 → gsm `outputs/a0d/inspect_v2.md`로 복사. v1 파일(사용자 판정 9쌍 포함)은 그대로 둠. 미리 본 결함 예: 1번 `마져 ⇒ 맞아.`에서 이웃한 부호 `.`가 편집 구간에 흡수돼 `[.→ᅧ]` 같은 부자연스러운 자모 치환이 생김 — 검수에서 이런 사례의 빈도를 확인할 것.

### 15:32–16:05 · 검수 대기 중 작업 계획 [gsm]

- 사용자 요청: A-0d v2 검수 동안 다음 작업 진행. 검수와 무관한 것만 고름 — (1) pcfg2 K4 남은 실험(좌분지·v=3·전종단 주석, `phdq_pcfg2/log.md`), (2) A-2 다음 항목 v0·v1 보간, (3) B-0 통과에 따른 B-1~B-3 신경 LM 재채점, (4) A-6 채널 베이즈화(VB). A-3(형태소 채널)은 검수 뒤.

### 15:40–15:55 · A-2 LM 보간(v0 영역 내 + v1 신문) [gsm, itcerdo]

- 작업: `hnc/morph_lm.py` — LM 증분 인터페이스 `begin/extend/end`(디코더가 KenLM을 직접 부르지 않게), 토큰 단위 선형 보간 `InterpLM`(P(w|h) = Σ w_k P_k(w|h), 상태 = 성분 상태 튜플, 가중치 0 성분은 싣지 않음), `load_lm("a,b", "w0,w1")`. `decode.py`·`rescore.py`·`eval_lm.py`에 `--lm` 쉼표·`--lm-weights`. `scripts/interp_lm.py`(성분별 토큰 log10 P를 한 번 구하고 격자 계산). 테스트 `tests/test_morph_lm.py` 5개(증분 = 문장 점수, 같은 모형 보간 = 항등, 토큰별 혼합 손계산, 가중치 0). 커밋 `9d48630`, `e64a465`.

```bash
python scripts/interp_lm.py      # w = v0 가중치, 격자 0~1
python scripts/eval_lm.py --tag v0 --lm datasets/lm/v0/lm5.bin,datasets/lm/v1/lm5.bin --lm-weights 0.8,0.2 --n 2000 --workers 12
python scripts/decode.py --set native --split dev --channel datasets/channel/ec2_native_em --lm <v0,v1> --lm-weights 0.8,0.2 --lam-lm 0.5,1,1.5,2 --lam-len 0 --tag native_interp08_em
python scripts/decode.py --records datasets/ec_gold/ec2_dev5k/records.jsonl --gold-m2 … --channel datasets/channel/ec2_native --lm <v0,v1> --lm-weights 0.8,0.2 --lam-lm 1,1.5,2,3 --tag ec2_dev5k_interp08_bm
```

- 결과 — dev 퍼플렉시티(v0 / v1 / **보간 w=0.8**): EC2 51.7 / 102.2 / **51.7**(집합별 최적 w=0.9: 50.9), Learner 41.2 / 51.2 / **36.2**, Lang8 55.7 / 80.8 / **49.8**, Native 22.7 / 39.3 / **19.3**; 묶음 49.2 / 82.7 / **46.2**(묶음·기하평균 모두 w=0.8 최적).
- 판별력(LM 원점수, 조사 삭제는 형태소당; v0 → v1 → 보간): 격조사 대치 EC2 .918 → .925 → **.937**, Learner .936 → .944 → **.951**, Lang8 .910 → .929 → **.934**; 어순 대부분 최고; 종결→연결어미는 v1보다 약간 낮음(EC2 .905 → .884, Lang8 .922 → .911).
- 디코딩 F0.5(전체):

| 집합 (채널) | v1 | **보간 0.8/0.2** |
|---|---:|---:|
| Kor-Native dev (EM) | 0.693 (λ_LM 0.5) | **0.734** (λ_LM 0.5; P .850 R .474), WS+SPELL .598 → .648 |
| EC dev 5k (BM) | 0.498 (λ_LM 2) | **0.518** (λ_LM 1) |

### 15:55–16:00 · ⚠ Kor-Native 공식 분할의 정답 중복 발견 [itcerdo]

- 보간 이득이 너무 커서 누출을 점검: **Kor-Native dev 정답 y의 67.1%(test 65.5%)가 train 정답에 그대로 있음**(오류문 x는 0%만 겹침 — 같은 정답 문장에 서로 다른 오류를 입힌 쌍이 분할을 가로질러 흩어짐). Learner 0.7%, Lang8 0.8~1.0%. EC2 dev는 v0 train 문장과 20%가 같음(짧은 상투 발화 위주). → 공식 분할 자체의 성질이라 train을 쓰는 모든 모형(seq2seq 포함)이 같은 혜택을 받지만, **영역 내 LM이 train 정답을 외워 Native 점수가 부풂.** 이전 Native 수치(0.671~0.717)도 v1이 v0 자료를 포함하므로 정도는 작지만 같은 영향을 받았음.
- 대응: `scripts/split_eval.py` — dev를 "정답이 train 정답에 있음(겹침, 1,768문장)"과 "없음(새 문장, 866문장)"으로 나눠 같은 가설을 다시 채점. **앞으로 Native는 전체와 새 문장 부분을 함께 보고한다.**

| Native dev (EM 채널, λ_LM 0.5) | 겹침 F0.5 | **새 문장 F0.5** |
|---|---:|---:|
| v1 | 0.711 | 0.642 |
| 보간 0.8/0.2 | 0.762 (+5.1) | **0.647 (+0.5)** |

- 해석: 보간 이득의 대부분은 겹침 부분(정답 암기). 새 문장에서는 +0.5(λ_LM 1.0이면 0.624 → 0.643, +1.8). 퍼플렉시티 개선도 같은 이유로 Native에서 과대평가됨(Learner·Lang8의 개선은 겹침 1% 미만이라 유효).

### 15:58–16:10 · B-1~B-3 신경 LM 재채점, B-5 PCFG 한계기여 재측정 [gsm, itcerdo]

- 환경: `.venv-nlm`(저장소 안, git 제외 — `.gitignore` 추가): torch 2.11.0+cu128, transformers 5.24.1, RTX 5090 32 GB 인식. pip는 `--no-cache-dir`, `HF_HOME`은 저장소 `.cache/huggingface`. 모델은 safetensors만 받음: `EleutherAI/polyglot-ko-1.3b`(인과, 2.8 GB), `klue/roberta-large`(마스크, 1.4 GB). 디스크 여유 45 → 35 GB.
- **B-1 결정(토크나이저 불일치 처리)**: 신경 LM은 **표층 문장 전체**를 자기 토크나이저로 채점한다. 문장 확률은 토큰화와 무관한 양이라 형태소 LM·PCFG 점수와 그대로 가중합할 수 있고, 형태소 ↔ 하위어 정렬이 필요 없다. 인과: log P(y)(BOS·EOS 포함), 마스크: PLL(Salazar et al. 2020, 토큰을 하나씩 가려 예측).
- 작업: `scripts/nlm_score.py`(n-best 고유 문장 채점 → `nlm_<이름>.jsonl`), `rescore.py --nlm/--lam-nlm`(점수 = λ_LM·LM − 채널 + λ_P·PCFG + λ_len·|m| + Σ λ_N·NLM), 격자 평가를 스레드 병렬로. 커밋 `c94d770`, `72cd760`.

```bash
python scripts/rescore.py --set native --split dev --channel datasets/channel/ec2_native_em --lm <v0,v1> --lm-weights 0.8,0.2 --base-lam-lm 0.5 --nbest 8 --tag native_interp_em
python scripts/rescore.py --records …/ec2_dev5k/records.jsonl --gold-m2 … --channel datasets/channel/ec2_native --lm <v0,v1> --lm-weights 0.8,0.2 --base-lam-lm 1.0 --nbest 8 --tag ec2_dev5k_interp_bm
.venv-nlm/bin/python scripts/nlm_score.py --tag <tag> --model EleutherAI/polyglot-ko-1.3b --kind causal --name pko13
.venv-nlm/bin/python scripts/nlm_score.py --tag <tag> --model klue/roberta-large --kind masked --name rbl --batch-size 512
python scripts/rescore.py … --from-nbest --nlm pko13,rbl --lam-lm … --lam-pcfg … --lam-len 0,1 --lam-nlm 0,0.1,0.25,0.5,1 --out-name summary_nlm.json   # 450 설정
python scripts/split_eval.py --set native --dirs outputs/rescore/native_interp_em --hyps …
```

- 결과: 보간 LM n-best 생성 Native 49 s(오라클 **0.825**), EC 51 s(오라클 0.560). 신경 LM 채점 2만 문장당 인과 11 s(GPU 3.4 GB), PLL 42 s.

| 재채점 설정 (dev에서 고른 λ) | Native 전체 F0.5 | **Native 새 문장** | EC dev 5k |
|---|---:|---:|---:|
| 1-best (형태소 LM 보간 + 채널) | 0.734 | 0.647 | 0.518 |
| + PCFG | 0.753 | 0.676 | 0.519 |
| + 인과 LM (polyglot-ko-1.3b) | 0.762 | 0.693 | 0.518 |
| + 마스크 LM (klue/roberta-large PLL) | 0.739 | – | 0.525 |
| + 인과 + 마스크 | 0.773 | **0.713** | 0.526 |
| + 인과 + 마스크 + PCFG | **0.776** (P .870 R .541) | 0.707 | **0.527** |
| n-best 오라클 | 0.825 | 0.800 | 0.560 |

  - 최고 λ: Native 전체 (λ_LM .5, λ_P .3, λ_pko .5, λ_rbl .1), 인과+마스크 (λ_LM .5, λ_pko 1, λ_rbl .1); EC (λ_LM 1, λ_P .1, λ_rbl .1). 길이 항(λ_len 1)은 어디서도 선택되지 않음.
- 해석: (1) **신경 LM 이득이 가장 큼 — 새 문장 부분에서 오히려 더 큼(+6.7)** → 정답 암기가 아닌 일반화. (2) **B-5(강한 LM 위의 PCFG 한계기여): Native 전체 +0.3, 새 문장 −0.6, EC +0.1 → 사실상 0.** 약한 LM(형태소 n-gram) 위에서는 +1.9 / 새 문장 +2.9였음 — 설계서 절제표 5행 대 8행의 대비가 실측으로 나옴. (3) EC 이득이 작은 것은 오라클 상한(0.56)과 범위 밖 부호 편집 때문. (4) λ를 450개 격자에서 dev로 골라 낙관적 → test는 마지막에 한 번만. 후보: 더 큰 인과 LM(polyglot-ko-5.8b, 12 GB).

### 16:00–16:13 · A-6 채널 베이즈화(희소 디리클레 VB-EM), 사후 집중 곡선, 채널 비교 [gsm, itcerdo]

- 작업: `hnc/channel_em.py` — `vb_em`(편집쌍 결합 분포 δ ~ Dir(α·1), 평균장 q(δ) = Dir(α + 기대 계수); E단계는 δ̃ = exp(ψ(a_k) − ψ(Σa))로 전·후진 계산; ELBO = Σ log Z̃ − KL(q‖p)), `digamma`(점근 급수, scipy 없음), `dirichlet_kl`, `conditional_posterior`(Dir의 부분 벡터 정규화 = Dir(a_{α,·})로 조건부 δ(β|α)의 사후 평균·표준편차를 닫힌 꼴로), `subsample`(창 계수 이항 표집), `em(max_len=)`. `scripts/vb_channel.py`(비율 0.1/0.25/0.5/1 → 곡선, 비율 1 → 디코더용 채널). 테스트 3개 추가(ψ·KL 값, ELBO 단조, 자료 10배 시 표준편차 감소) → 6 passed. 커밋 `6d383d5`, `0aa18cb`.

```bash
python scripts/vb_channel.py --alpha 0.1                       # 비율 4개, 각 1.7~4.7 s
python scripts/vb_channel.py --alpha 0.01 --fracs 1
python scripts/vb_channel.py --alpha 0.1 --fracs 1 --max-len 2
python scripts/decode.py … --channel datasets/channel/{ec2_native, ec2_native_em, ec2_native_vb_a0.1} --lm <v0,v1> --lm-weights 0.8,0.2
```

- 결과 — **ELBO 단조 증가**(모든 비율, 예: 비율 1 −80,280,143 → −78,756,308). **사후 집중 곡선**(BM 계수 상위 20개 편집의 조건부 확률 변동계수 중앙값): 비율 0.1 → 0.25 → 0.5 → 1: **0.0148 → 0.0085 → 0.0057 → 0.0039**(자료 10배에 3.8배 감소 ≈ N^{-1/2}). 활성 편집쌍(기대 계수 ≥ 0.5) 5,287 → 9,032 → 13,268 → 19,014(α < 1의 희소화: 자료가 적을수록 적은 편집만 남음).
- 결과 — 긴 조각 퇴화는 **그대로**: dev 창당 로그우도(사후 평균 δ) 초기 −9.00 → 1회 −9.73 → 5회 −9.61(α = 0.1), −9.63(α = 0.01) — MAP-EM(−9.64)과 같음. 최대 조각 길이 2로 줄여도 −10.14 → −10.60(전체 수준만 낮아지고 같은 하락). → 원인은 조각 길이가 아니라 **결합 분할 EM의 부익부**(학습 창의 문맥에 묶인 편집으로 질량이 이동해 처음 보는 문맥으로 일반화가 약해짐). 희소 사전분포는 큰 계수를 건드리지 못하므로 해결책이 아님. 후보: 문맥 편집을 문맥 없는 편집에서 생성하는 계층 사전분포(기저 측도 = 짧은 편집의 합성, Goldwater류), 또는 문맥 편집을 분할 잠재변수 밖에 두는 조건부 모형.
- 결과 — 채널 비교(보간 LM, λ_LM 0.5 / EC는 1.0; F0.5 전체, Native는 새 문장 부분·WS+SPELL 함께):

| 채널 | Native 전체 | Native 새 문장 | Native 새 문장 WS+SPELL | EC dev 5k |
|---|---:|---:|---:|---:|
| Brill–Moore (A-1b) | 0.711 | 0.628 | **0.565** | **0.518** |
| MAP-EM (A-1c) | 0.734 | 0.647 | 0.522 | 0.508 |
| VB-EM α 0.1 (A-6) | **0.735** | **0.649** | 0.524 | 0.508 |

- 해석: VB ≈ MAP-EM(사후 평균이 MAP과 거의 같은 큰 자료 영역). EM 계열은 Native의 WS+SPELL 밖 유형(구어체 어미 등 문맥 의존 편집)을 더 고쳐 전체 F0.5가 오르지만, 띄어쓰기·맞춤법 자체와 EC에서는 BM이 낫다 — 퇴화와 같은 방향. 기본 채널 결정은 여전히 보류(유형별 평가가 생기는 A-3 뒤에 결정).

### 16:17–16:35 · 파일 정리: 불필요 파일 삭제, 기록 파일은 T31로 [gsm, itcerdo]

- 사용자 요청: 불필요한 파일은 지우고 기록만 남길 파일은 `/Volumes/T31`에 디렉토리를 만들어 옮김. 분류안(삭제·이동·유지)을 사용자가 승인, 보관 위치 `/Volumes/T31/phdq_prob`, LM v1 코퍼스는 압축 보관 후 삭제로 결정.
- 이동(itcerdo → T31, 저장소 기준 상대 경로 유지): itcerdo에서 SHA-256 목록을 먼저 만들고 tar 스트림으로 옮긴 뒤 T31에서 `shasum -a 256 -c`로 **278개 전부 일치** 확인 후 삭제. 대상: `outputs/{a0b,a0c,a0d/run.log,a0d_kor,a1,a1c,a1d,a2,a6,ec_eval,kagas,m2smoke,interp_lm,kenlm/build.log}`, 대체된 n-best `rescore/{native_em,ec2_dev5k_bm}`, `rescore/*.log`, 기타 로그. 현재 n-best의 요약 10개는 사본만(원본 유지). gsm의 v1 검수 파일(사용자 판정 9쌍)은 `outputs/a0d/inspect_v1_user_judged.md`로 이동.
  - 처음 목록에서 `outputs/a0d/run.log`, `rescore/native_em.log`·`ec2_dev5k_bm.log` 3개가 빠져 있어 삭제 전 점검에서 발견 → 같은 방식으로 옮기고 확인한 뒤 삭제.
- LM v1 학습 코퍼스 `datasets/lm/v1/train.txt`(11.7 GB): itcerdo에서 `zstd -T16 -10` → 1.8 GB, `zstd -t` 통과, T31로 전송 후 압축 파일 해시 일치, **로컬에서 압축 해제한 스트림의 SHA-256이 원본과 일치**(61eb1e9c…) 확인 후 원본 삭제.
- 삭제(재생성 가능 또는 원본이 T31에 있음): 신문 2024·2025 압축 해제본 7.4 GB(원본 zip은 `/Volumes/T31/NIKL`), pip·pytest 캐시, kenlm smoke 파일, 재채점 격자 가설 `hyp_*.txt`·`split_eval/`(n-best + 신경 LM 점수로 재생성), 실험용 채널 `ec2_native_vb_a0.01`·`_vb_a0.1_L2`(수 초면 재생성).
- 결과: itcerdo 여유 33 GB → **57 GB**(phdq_prob 35 → 16 GB, phdq_pcfg2 4.1 → 0.9 GB). 정리 뒤 테스트 prob 89 passed, pcfg2 71 passed. 남은 `outputs/`: `a0d/inspect_v2.md`(검수 중), `rescore/native_interp_em`·`ec2_dev5k_interp_bm`(n-best·신경 LM 점수·요약). 보관 목록·무결성 확인·재생성 방법은 `/Volumes/T31/phdq_prob/README.md`. `CLAUDE.md`에 보관 규칙 추가.

### 16:35–16:45 · A-0d 육안 검수 v2 결과 [gsm]

- 사용자 검수 완료(`outputs/a0d/inspect_v2.md`, 내용 편집 쌍 100개 · 편집 구간 136개): **ok 128(94.1%), bad 8(5.9%)**. bad 8개의 이유는 모두 "널리 쓰는 (구어) 표현 / 표준어에 없는 구어 표현" — **정렬 오류는 0개**이고, 교정 정답이 오류가 아닌 통용 표현을 규범형으로 바꾼 경우다.

| 유형 | 개수 | 예 (원문 x ⇒ 교정 y) |
|---|---:|---|
| 외래어 표기 규범화 | 3 | 아울렛 ⇒ 아웃렛, 샤베트 ⇒ 셔벗, 라떼 ⇒ 라테 |
| 구어 부사 → 표준 부사 | 2 | 완전 ⇒ 정말 (2회) |
| 채팅 약어 → 감탄사 | 2 | ㅇㅇ ⇒ 응응, ㅇㅇㅇ ⇒ 응응응 |
| 비표준 어휘 | 1 | 널널 ⇒ 널찍 |

- 함의: (1) **A-0d 정렬 파이프라인 통과**(구간 대응·형태소 투영·자모 편집 모두 수용). (2) EC 정답에는 오타·문법 오류와 다른 **"통용 변이의 규범화"** 부류가 있음 — 자모 채널로 설명하면 무의미한 편집이 됨(완전 → 정말 = 자모 7개). → A-3의 z에 이 부류를 따로 둔다(어휘 층위 대치이되 "오류 아님 — 규범화"로 설명). (3) 문장부호가 편집 구간에 흡수된 경우(예: `그려 ⇒ 그래,`의 `[,→ᅧ]`)는 사용자가 ok로 판정 — A-3의 형태소 편집에서는 부호 형태소(S* 품사)를 떼고 비교한다.
- A-0d 완료. 다음: A-3(형태소 채널).

### 16:45–17:15 · A-3a 형태소 편집 추출·자동 z [gsm, itcerdo]

- 작업: `hnc/morph_channel.py` — A-0d 편집 구간(검수 통과)의 y 형태소(정렬 투영)와 x 형태소(오류문 x의 Kiwi 분석 = m′의 근사)를 부호 형태소를 뗀 뒤 같은 부류끼리 싸게 대치하는 레벤슈타인으로 정렬하고 편집마다 자동 z를 붙인다: PARTICLE(조사 대치)·ENDING(어미 대치)·LEXICAL(어휘 대치)·VARIANT(통용 변이 규범화)·SPELL(표기)·DEL(쓴 사람이 빠뜨림)·INS(더 씀). `scripts/extract_morph_edits.py`(문맥 c = 품사 + 앞 표층 문자의 받침, 왼쪽 품사 lt, y 형태소 단일·조사어미 이중 빈도; Kor-*는 KAGAS 유형과 구간 부류 대조). 테스트 8개.
  - **규칙 다듬기(실측 근거)**: (1) 1차 실행에서 KAGAS 어휘 오류의 47%가 VARIANT로 잡힘 — "원문 형태소가 정문 말뭉치에서 흔함"만으로는 학습자의 실재어 오용(나 → 저, 오 → 가)과 구별 안 됨. 형태소 단위 빈도는 오타도 실재 형태소로 분석되어(잇어서 → 잇/NNG, 확률 −5.08 = 아울렛과 같음) 쓸 수 없음 → **VARIANT = 원문 어절 표기가 신문+교정문 어절 빈도에서 ≥5회·교정 표기의 0.1배 이상 ∧ (자모 ≤2개 차이의 표기 변이 ∨ EC에서 3회 이상 규범화되는 쌍)**. EC에서 모은 체계적 규범화 쌍 245개(응응/ㅇㅇ 728, 티브이/티비 142, 돈가스/돈까스 98, 정말/완전 54, 파이팅/화이팅 50, 라테/라떼 40 …). (2) 오타가 든 x는 Kiwi가 엉뚱하게 쪼갬(날시 → 날/NNG + 시/NNG, 여황 → 여/MM + 황/NNG) → 내용 형태소가 바뀌었어도 구간 표층이 자모 몇 개 차이면 구간 전체 SPELL(m′ = m, 자모 채널 몫). 조사·어미만 다른 경우(을 ↔ 를)는 제외. (3) 조사 누락·과잉을 KAGAS가 PARTICLE로 부르므로 대조는 구간 부류(조사가 관여하면 PARTICLE)로.
- 검수 판정 대조(`--inspect-check`, EC dev 136구간): 사용자 bad 8개 중 **VARIANT 5개**(아울렛, ㅇㅇㅇ, 완전 ×2, 라떼), 놓친 3개는 ㅇㅇ(띄어쓰기 누락으로 원문 어절이 "ㅇㅇ수박이맛있었는데"가 되어 빈도 판정 실패)·널널·샤베트(체계 쌍도 표기 변이도 아님 — 의미 지식 필요). ok 128개 중 VARIANT는 3개(그려 → 그래, 키로 → 킬로, 걸 → 거 — 모두 구어·외래어 변이라 오분류는 아님).
- 결과(train, 73 s):

| 자료 | 쌍 | 편집 구간 | 구간 z 상위 | KAGAS 부류 일치 (PARTICLE / ENDING / LEXICAL / SPELL) |
|---|---:|---:|---|---|
| Kor-Learner | 19,898 | 41,718 | 조사 9,373 · 삭제 8,898 · 표기 7,409 · 어미 6,163 | **76.3%** (98.9 / 94.5 / 57.9 / 52.5) |
| Kor-Lang8 | 76,692 | 156,875 | 삭제 33,158 · 어휘 31,749 · 어미 27,675 | 59.2% (96.0 / 92.5 / 54.8 / 40.0) |
| Kor-Native | 12,292 | 8,309 | 어미 2,965 · 표기 1,986 | 54.9% (93.4 / 72.5 / 54.7 / 42.2) |
| EC(무작위 30만) | 300,000 | 95,634 | 표기 51,125 · 어미 22,322 · 어휘 9,893 · 변이 4,344 | — |

- 해석: 조사·어미는 KAGAS와 93~99% 일치. 불일치의 주류는 KAGAS SPELL ↔ 자동 ENDING(돼요 → 되요처럼 "어미가 바뀜(m′ = 되+요)"과 "자모 오타(ㅙ → ㅚ)"가 둘 다 성립) — 규칙 z는 초기값이고, 이 모호성을 사후분포로 푸는 것이 모형의 몫(설계서 §1.5).

### 17:15–17:40 · A-3b·c 형태소 채널 추정·디코더 통합·설명 출력 [gsm, itcerdo]

- 작업: `MorphChannel`(생성 방향 확률 — 대치 P(m → m′) = N/(N(m)+β), 삭제 P(DEL|m, lt)는 조사·어미의 (lt, m) 빈도로 평활, 삽입 P(INS m′|lt), 동일 P(IDENT|m)은 품사 비율로 평활, β = 5, 빈도 2 미만 사건 제외), `scripts/fit_morph_channel.py`(learner = Kor-Learner + Lang8, all = 4자료). 디코더: x를 Kiwi로 한 번 분석해 어절별 m′를 얻고 조사·어미 대치·과잉 제거·누락 복원 후보를 하나씩 적용해 **Kiwi join으로 실현**(이형태 을/를·이/가·으로/로, 축약 되+어요 → 돼요, 불규칙 돕+어요 → 도와요를 join이 처리 — 실측 확인), 비용 = 원문 동일 비용 + Δ_M, 같은 표층이 두 경로에서 나오면 확률 합. `explain()`: 바뀐 어절마다 경로별 채널 확률을 정규화한 P(z|x,ŷ). `decode.py --morph-channel/--type-eval`, `scripts/explain.py`. 테스트 100 passed.
  - 오류·수정 2건: (1) 처음엔 내용 형태소 후보까지 냈더니 Kor-Learner 300문장에서 복수 들 추가(사람 → 사람들)·내용어 삭제(지금는 → 는)·어휘 대치(지 → 후) 오탐이 대부분 → **후보를 조사·어미로 한정**(F0.5 0.110 → 0.225). (2) 삭제 복원 확률의 분모(왼쪽 품사 이중 빈도)를 조사·어미만 세면서 다른 품사(하/XSV, 것/NNB)는 분모가 0이라 확률 1(비용 0)이 되어 후보 자리를 차지 → 조사·어미만 추정하도록 수정. 문장 처음(BOS) 삽입도 분모가 없어 제외.
  - 학습자 집단 자모 채널: A-1a 추출 출처에 learner·lang8 추가 → `datasets/channel/learner_lang8`(쌍 96,590, 편집 25,796). EC·Native 채널은 학습자 글의 띄어쓰기를 과교정(유창하게 → 유창 하게, 두 시에 → 두시에)하는데 Kor-Learner 정답은 띄어쓰기를 거의 고치지 않음(train WS 109개).
- 300문장 시험(λ_LM 1.0): EC·Native 자모 채널 0.101 → 학습자 자모 채널 0.211 → **+ 형태소 채널(조사·어미) 0.274**.

### 17:40–18:45 · A-3 Kor-Learner dev 전체 평가, 설명 z 정확도, A-3d 계층 분리 [gsm, itcerdo]

```bash
C="--set korean_learner --split dev --lm <v0,v1> --lm-weights 0.8,0.2 --type-eval PARTICLE,ENDING,SPELL,NOUN,VERB,CONJUGATION"
python scripts/decode.py $C --channel datasets/channel/{ec2_native_em | learner_lang8 | learner_lang8_J} [--morph-channel datasets/morph_channel/{learner|all}]
python scripts/explain.py --set korean_learner --split dev --channel … --morph-channel datasets/morph_channel/learner --lam-lm 1.5
python scripts/make_jamo_only_pairs.py; python scripts/extract_edits.py --sources learnerJ,lang8J --tag learner_lang8_J; python scripts/fit_channel.py --tag learner_lang8_J
```

- 결과 — Kor-Learner dev 4,264문장(정답 중복 0.7%라 "새 문장" 구분 불필요), F0.5와 KAGAS 유형별 재현율(λ_LM별 최고):

| 자모 채널 | 형태소 채널 | λ_LM | P / R / **F0.5** | R 조사 | R 어미 | R 표기(SPELL) |
|---|---|---:|---|---:|---:|---:|
| EC2+Native EM (A-1) | — | 1.5 | .158 / .034 / 0.092 | 0.7% | 3.0% | 15.9% |
| 학습자(Learner+Lang8) | — | 1.5 | .296 / .091 / 0.204 | 11.4% | 8.7% | 25.0% |
| 학습자 | learner | 1.5 | .292 / .120 / 0.227 | **18.1%** | **11.1%** | 25.4% |
| 학습자 | all(4자료) | 1.5 | .304 / .114 / 0.228 | 16.4% | 10.9% | 25.2% |
| **학습자·자모 층위만(A-3d)** | learner | 1.5 | .324 / .145 / **0.260** | 8.6% | 8.5% | **65.1%** |

- 결과 — 설명 P(z|x,ŷ)의 z 정확도(시스템 교정이 정답 편집과 같은 참 양성에서 argmax z 부류 대 KAGAS 유형; λ_LM 1.5):

| 자모 채널 | 참 양성 구간 | **비교 가능 유형 정확도** | 조사 | 어미 | 표기 |
|---|---:|---:|---:|---:|---:|
| 학습자(모든 편집으로 학습) | 1,015 | 60.5% | 46.7% (237/445가 JAMO로 설명) | 42.5% | 94.7% |
| **학습자·자모 층위만(A-3d)** | 1,230 | **93.4%** | **97.1%** | 54.9% | 98.0% |

  - NOUN·VERB 등 어휘 유형(참 양성 225개)은 이 모형이 어휘 후보를 내지 않아 모두 JAMO로 설명됨(표기가 비슷한 어휘 교정).
- **A-3d 계층 분리**: 자모 채널을 모든 편집으로 학습하면 조사·어미 오류(는 → ∅, 를 → 을)도 자모 편집으로 흡수해 설명이 자모 경로로 쏠림 → `make_jamo_only_pairs.py`로 A-3a z가 SPELL인 편집 구간과 띄어쓰기·부호 구간만 x 쪽으로 남긴 x′(형태소 층위 편집 구간은 y로 되돌림; Kor-Learner 34,309구간 되돌림, 불일치 0)를 만들어 자모 채널을 다시 학습. 설계서 캐스케이드(P_J는 m′ = m의 자모 잡음만)를 데이터 수준에서 맞춘 것.
  - 해석: (1) **설명 정확도 60.5 → 93.4%** — 층위 분리가 z 식별의 조건. (2) F0.5도 0.227 → 0.260, 표기 재현율 25 → 65%(자모 채널이 형태소 오류에 확률을 나눠 주지 않음). (3) 대신 조사 재현율 18.1 → 8.6%: 자모 채널이 대신 고치던 조사 오류를 형태소 채널(관측 쌍 ≥ 2, 어절당 편집 1개, 후보 12개, 어휘 필터)이 다 덮지 못함 → 형태소 채널 범위 확장이 다음 과제. (4) 사후분포가 대부분 1.00 — 같은 표층을 두 경로가 함께 만드는 경우가 드묾(경로가 겹칠 때만 모호성이 생김).
- 학습자 신경 LM 재채점(학습자 자모 채널 + 형태소 채널 n-best 8, 오라클 0.360): 기준 0.227 → + PCFG 0.229 → **+ 인과 LM(polyglot-ko-1.3b) 0.280**(P .384) → 마스크 LM·PCFG는 더해도 그대로. 오라클이 낮아(0.36) 후보 생성이 상한.
- 계층 분리 모형(학습자·자모 층위 자모 채널 + 형태소 채널)의 n-best 8 재채점(오라클 0.353): 기준 **0.260** → + PCFG 0.265 → **+ 인과 LM 0.306**(P .404 R .155; λ_LM 0.5, λ_pko 1.0) → + PCFG까지 0.306. 마스크 LM은 더하지 않음.
- **Kor-Learner dev 누적: 0.092(A-1 자모 채널) → 0.204(학습자 자모 채널) → 0.227(+ 형태소 채널) → 0.260(계층 분리) → 0.306(+ 인과 LM 재채점)**. λ는 dev에서 고른 값.
- 설명 표본(사람 검토용, 말뭉치 문장 포함 — git 제외): gsm `outputs/a3/explain_sample.md`.

### 19:03–19:25 · A-3e 형태소 후보 포괄률 진단·확장 [gsm, itcerdo]

- 작업: `scripts/cand_coverage.py` — 정답 M²의 한 어절 편집에 대해 교정 어절이 그 어절의 디코더 후보에 들어 있는지(유형별, 형태소 경로/자모 경로). `DecoderConfig`에 `morph_max_cands`·`morph_two_edits`(어절당 형태소 편집 2개)·`morph_combine`(자모 후보 위에 형태소 편집), `decode/rescore/explain --morph-*`.
- 진단(Kor-Learner dev 앞 1,500문장, 한 어절 편집 2,965개): 계층 분리 모형의 조사 교정 포괄률 **18.9%**(형태소 경로 145/847) — 분리 전엔 자모 채널이 23%를 더 덮었음. 놓친 것을 보니 대부분 **흔한 조사의 누락 복원**(우리 → 우리는, 한국말 → 한국말을)과 흔한 대치(방을 → 방에). 원인: 후보를 조건부 확률 P(DEL|m, lt) 순으로 골라, 빈도가 작아 비율이 큰 형태(축약형 ᆫ/JX 등)가 자리(왼쪽 품사당 8개)를 차지. → **후보 선택은 사건 빈도순, 점수는 채널 확률**(사전확률은 LM 몫)로 바꾸고 대안 수 20. 테스트 1개 추가(101 passed). 그 밖에 Kiwi 오분석(저 → IC, 아이 → IC)과 축약 실현 차이(게 ↔ 것이)가 남은 원인.

| 형태소 후보 설정 | 조사 포괄 | 어미 포괄 | 표기 포괄 | 전체 포괄 | 어절당 후보 |
|---|---:|---:|---:|---:|---:|
| 비율순, 12개 (이전) | 18.9% | 13.1% | 68.3% | 20.6% | 10.0 |
| 빈도순, 12개 | 66.9% | 41.3% | 69.0% | 39.2% | 11.3 |
| **빈도순, 30개** | **86.1%** | **61.4%** | 70.6% | **49.8%** | 14.6 |
| + 어절당 편집 2개 | 86.1% | 63.4% | 71.0% | 50.3% | 15.1 |
| + 자모 후보 3개 위 형태소 편집 | 86.5% | 62.7% | 71.9% | 51.8% | 21.1 |

- 디코딩(Kor-Learner dev 전체, 계층 분리 자모 채널 + 형태소 채널 learner, 빈도순 30개): λ_LM 1.0 **P .405 R .230 F0.5 0.352**(조사 재현율 8.6 → **28.6%**, 어미 8.5 → **18.8%**, 표기 65.8%), λ 1.5 0.340, λ 2.0 0.326. 문장당 0.26 s. 자모 후보 위 형태소 편집(3개)은 0.352로 같고 느려(0.30 s) 쓰지 않음. → **신경 LM 없이 이전 최고(0.306, 인과 LM 재채점)를 넘음.**

### 19:10–19:35 · A-3f z의 EM 재추정(E단계·M단계), A-3d를 EC·Native에도 [gsm, itcerdo]

- 작업: `extract_morph_edits.py`가 표층 표기 판정으로 SPELL로 돌린 구간의 원래 형태소 편집(`edits_raw`)을 보존(EM에서 두 가설을 함께 보기 위함). `scripts/em_z.py`(E단계): 구간마다 자모 경로 비용 = Σ −log P(IDENT|m) − log P_J(x_구간|y_구간)(분리 자모 채널, WFST 비터비)과 형태소 경로 비용 = Σ 편집 비용(형태소 채널 표; 없으면 12 nats) − log P_J(x|x) → P(형태소 경로|x, y) = σ(차). M단계: `MorphChannel.fit`이 구간 사후확률 `p_morph`로 가중 계수, `make_jamo_only_pairs --em`(p_morph < 0.5인 구간만 자모 층위로 남김) → 자모 채널 재학습(`learner_lang8_JE`), 형태소 채널 `learner_em`.
- E단계 결과: 사후확률은 대부분 0 또는 1 근처(규칙 z가 대체로 확신 있는 쪽). KAGAS 부류 일치 Kor-Learner **76.3 → 77.1%**, Lang8 59.2 → 60.2%. 뒤집힌 것: 규칙 ENDING → SPELL 1,008, PARTICLE → SPELL 763, LEXICAL → SPELL 580, DEL → SPELL 340(Learner; 자모 채널이 더 싸게 설명), SPELL → INS 68 등.
- A-3d EC·Native: `extract_morph_edits --sets ec2`(EC 2벌식 train 전부 899,544쌍, 편집 구간 314,558: SPELL 171,298, ENDING 73,404, LEXICAL 32,396, VARIANT 13,113, DEL 11,734, INS 8,512, PARTICLE 4,101) → 자모 층위 쌍(되돌린 구간 EC 143,260 / Native 6,323) → 자모 채널 `ec2_native_J`, 형태소 채널 `native_ec2`. 주의: EC 형태소 대치 최다가 `었 → 엇`(12,401) — 사실상 ㅆ → ㅅ 타자 오타인데 Kiwi가 x를 엇/EP로 분석하고 규칙은 조사·어미끼리의 차이를 형태소 편집으로 두므로 ENDING이 됨. 이런 모호성이 EM(자모 경로가 ㅆ → ㅅ을 내용어 오타에서 배워 더 싸게 설명)이 풀 대상.

### 19:35–19:47 · A-3 결과: 학습자 신경 LM 재채점, EM 효과, EC·Native 계층 분리 [itcerdo]

```bash
python scripts/rescore.py --set korean_learner --split dev --channel datasets/channel/learner_lang8_J --morph-channel datasets/morph_channel/learner --morph-max-cands 30 --base-lam-lm 1.0 --nbest 8 --tag learner_JJ_Ml30   # + nlm_score pko13·rbl + --from-nbest 격자
python scripts/decode.py --set korean_learner … --channel datasets/channel/learner_lang8_JE --morph-channel datasets/morph_channel/learner_em --tag learner_JE_Mem
python scripts/explain.py … --lam-lm 1.0 --tag {learner_JJ_Ml30 | learner_JE_Mem}
python scripts/decode.py --set native … --channel datasets/channel/{ec2_native_J | ec2_native_em} [--morph-channel datasets/morph_channel/native_ec2 [--morph-max-cands 12]]
```

- **Kor-Learner 신경 LM 재채점**(빈도순 30개 모형 n-best 8, 오라클 0.494): 기준 0.352 → + PCFG 0.353 → **+ 인과 LM 0.408**(P .466 R .274; λ_LM 0.5, λ_pko 1.0) → 마스크 LM·PCFG를 더해도 그대로. → **Kor-Learner dev 누적: 0.092 → 0.204 → 0.227 → 0.260 → 0.352 → 0.408.**
- **설명 z 정확도**(빈도순 30개 모형, λ 1.0): 참 양성 1,971구간(이전 1,230), 비교 가능 유형 **93.4%** — 조사 **99.9%**(703), 어미 88.7%(204, 이전 54.9%), 표기 87.6%(630).
- **EM(A-3f) 효과 없음**: M단계 재학습 모형(자모 `learner_lang8_JE` + 형태소 `learner_em`) F0.5 0.351(규칙 z 모형 0.352), z 정확도 93.0%(93.4%). E단계 사후확률이 대부분 0·1 근처라 규칙 z와 거의 같은 할당 → 규칙 z를 기본으로 유지, EM은 도구로 남김(설계서의 "지도 초기화 → 비지도 확장"은 태그 없는 대규모 자료에서 다시 볼 것).
- **EC·Native에서는 계층 분리 + 형태소 채널이 오히려 나쁨**(보간 LM, λ_LM 0.5):

| Kor-Native dev 설정 | F0.5 전체 | 새 문장 | WS+SPELL |
|---|---:|---:|---:|
| 기존 EM 자모 채널(모든 편집) | **0.734** | **0.647** | 0.648 |
| 분리 자모 채널만 | 0.686 | – | **0.706** |
| 기존 EM 채널 + 형태소 채널 12개 | 0.726 | – | 0.629 |
| 기존 EM 채널 + 형태소 채널 30개 | 0.710 | – | 0.612 |
| 분리 자모 채널 + 형태소 채널 30개 | 0.696 | 0.622 | 0.633 |

  - EC dev 5k: 분리 + 형태소 0.508(기존 BM 0.518).
  - 해석: (1) 분리는 띄어쓰기·맞춤법을 개선(0.648 → 0.706)하지만, 모어 화자 자료의 나머지 편집(구어 어미 규범화 등)을 문맥 묶음 자모 편집으로 고치던 몫을 잃음. (2) 모어 화자 글에서는 형태소 후보(조사 추가 등)가 LM 선호로 오탐을 늘림(정밀도 0.85 → 0.80). (3) **집단별 결론: 학습자 = 계층 모형(자모 층위 채널 + 형태소 채널)이 압도적, 모어 화자 = 기존 자모 채널이 최선.** 설계서 §6.1의 "집단별 채널 혼합"이 실측으로 필요해짐. 형태소 채널 가중(λ_M)·집단 혼합은 후속.

### 19:51 · A-3g 집단별 채널 혼합 실험 설계 [gsm, itcerdo]

- 작업: 후보 수(30)와 보간 LM(v0:v1 = 0.8:0.2)을 고정한다. 두 효과를 분리한다: 자모 편집별 학습자 가중치 ρ(모어 `ec2_native_em` ↔ 학습자 `learner_lang8_J`), 그리고 형태소 경로 질량 배수 λ_M(0 = 끔, 1 = 기존). 형태소 표는 세 자료 모두 `learner`로 고정한다. 이는 문장으로 화자 집단을 예측하는 분류기가 아니다.
- 명령: 원격 `git status`, 채널 매개변수·dev 레코드 필드·디스크 확인. 기준 커밋 `0a3a61d`.
- 판정 기준(실행 전): ρ ∈ {0, 0.5, 1}, λ_M ∈ {0, 0.1, 1, 3}; Kor-Learner·EC에서 λ_LM=1, Native에서 0.5. dev를 고정 해시로 선택/확인 두 부분으로 나누고 선택 부분의 M² F0.5만으로 조합을 고른다. 확인 부분에서 기존 집단별 기준과 비교한다(EC의 기존 BM은 별도 대조군). Native는 train과 정답이 다른 부분도 따로 본다. 신경 LM, test, 어휘 후보는 이 비교에서 바꾸지 않는다.
