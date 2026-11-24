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
- 개선 ①(`…`): 이미 편집된 자모는 다시 편집하지 않음(겹치지 않는 분할), 후보 비용을 **분할 비용**(바뀌지 않은 자모의 동일 비용 + 편집 비용; WFST 최단 거리의 상한)으로 직접 계산해 디코딩 중 WFST 호출 제거(`exact_channel` 옵션으로 유지), 2단계 확장 상위 40 → 20. → 30문장 42.5 s.
- 개선 ②: 문맥 없는 순수 삽입(β = ∅) 101개를 모든 경계에 넣던 것을 비용 상위 8개로 제한(`<SP>`·`<SP><SP>` 등; 같은 패턴의 문맥 붙은 형태는 제자리에서만 적용됨). → 100문장 문장당 0.57 s.

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
