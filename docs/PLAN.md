# game-lab 분리 계획

작성: 2026-11-13. 상태: **분리 완료(2026-11-13).** 실측과 실험 계획의 남은 단계는 사용자와 정한 뒤 시작.

ARMY 게임 플러그인(`codex/game-adaptive-perception`, `92ccefe5f`)에서 게임 에이전트만 떼어 낸 독립 하네스다.
혼자 개발하며 성능을 보는 것이 목적이고, ARMY와의 결합은 나중 문제다. ARMY 저장소는 읽기만 한다.

## 1. 결정 (사용자, 2026-11-13)

- 새 저장소, 로컬에만 둔다. 원격 push 없음.
- ARMY의 안전 장치는 다시 만들지 않는다: Sentinel 승인, 입력 승인 영역, 입력 직전 픽셀 재검사, 한 번만 보내기·영수증, task 저장·lease.
- 남기는 것은 게임 로직으로서의 결과 확인이다. 수 뒤 화면을 규칙 모델의 예상 전이와 대조하고, 어긋나면 복구하거나 멈춘다.
- 웹 게임(Playwright)과 모바일 게임(ADB)을 모두 측정한다.

## 2. 폴더 구조

```text
game-lab/
├─ pyproject.toml            Python 3.14, .venv (Pillow, jsonschema, playwright, httpx, pytest)
├─ docs/
│  ├─ PLAN.md                이 문서
│  └─ origin/                출발 문서 사본(실험 계획, 게임 에이전트 조사). 읽기 참고용
├─ src/gamelab/
│  ├─ contracts.py           세션·계열·환경이 주고받는 모양 (FamilyModule, EnvironmentAdapter)
│  ├─ session.py             관측 → 추정 → 계획 → 입력 → 결과 확인 루프
│  ├─ errors.py              로컬 오류 타입 (kernel.errors 대신)
│  ├─ resources.py           blueprint JSON 읽기와 스키마 검사 (kernel.blueprint 대신, jsonschema)
│  ├─ catalog.py             계열 목록: 규칙 모델, 풀이기, reader, controls
│  ├─ rules/  solvers/       등록 규칙 모델과 풀이기 (그대로)
│  ├─ adapters/              SiteAdapter 계약과 기준 어댑터 reference.py
│  ├─ families/              GridFamily, 계열별 reader·controls·discover
│  ├─ perception.py geometry.py pixels.py pieces.py profiles.py discovery.py   판독 도우미
│  ├─ execution.py           행동 → 입력 매핑
│  ├─ model.py               모델 클라이언트 (OpenAI 호환 API, 기본 OpenRouter)
│  ├─ vision.py              VLM 판정·보정·알아보기 질문 (CorrectionReader)
│  ├─ environments/
│  │  ├─ replay.py           시뮬레이션 게임 (시험, 정책 평가)
│  │  ├─ screen.py           화면 환경 공통: 판 측정, 판독, 덮개·끝 화면 판정, 입력. Surface 위에서 돎
│  │  ├─ web.py              Surface: Playwright(Chromium)
│  │  └─ android.py          Surface: ADB
│  ├─ play.py                한 판 실행 (ARMY의 task.py 자리. 인자는 CLI에서)
│  ├─ blueprint/             계열 설명, 프로필, 스키마, 프롬프트
│  └─ eval/                  평가 CLI: policies, web, android (+ sites.json)
└─ tests/                    fixtures/ 포함
```

## 3. 옮길 것, 버릴 것, 새로 쓸 것

| 구분 | 대상 | 처리 |
|---|---|---|
| 그대로 옮김 | `rules/`, `solvers/`, `families/`, `adapters/`, `contracts.py`, `execution.py`, `perception.py`, `geometry.py`, `pixels.py`, `pieces.py`, `profiles.py`, `discovery.py`, `environments/replay.py`, `blueprint/`(계열, 프로필, 스키마, 프롬프트) | import만 `gamelab`으로 |
| 고쳐 옮김 | `session.py` | `kernel.errors` → `errors.py`. 루프와 복구 로직은 그대로 (의도 기록과 영수증 모양은 세션 기록의 일부로 남지만, 환경이 채우는 값은 단순해짐) |
| 고쳐 옮김 | `catalog.py`, `profiles.py` | `kernel.blueprint` → `resources.py`, `kernel.i18n` 검사 삭제 |
| 고쳐 옮김 | `vision.py` | ARMY 라우터 → `model.py`. tenant·user·task 범위는 실행 id 하나로 |
| 고쳐 옮김 | `families/block_placement/controls.py` | `kernel.pointer_input` 상한 → 로컬 상수 |
| 새로 씀 | `environments/screen.py` | ARMY `environments/web.py`에서 게임 로직(판 측정, 다시 측정, 못 읽는 화면의 VLM 판정, 덮개 닫기, 멈춘 판 판정, 프로필 없는 게임 알아보기)만 가져옴. 승인·증거·영수증 검증은 뺌 |
| 새로 씀 | `environments/web.py`, `environments/android.py` | Surface 구현: 캡처, 판 영역 캡처, DOM 읽기(웹), 키, 탭, 드래그 |
| 새로 씀 | `model.py`, `play.py`, `eval/` | 모델 직접 호출, 한 판 실행, 평가 CLI |
| 버림 | `task.py`, `plugin.py`, `intake.py`, `task.schema.json`, `agents/specialists.json`, ARMY `environments/web.py`의 승인·visual proof·input session | ARMY 결합 |
| 버림 | 시험 중 Sentinel·승인·task 재개·말로 한 요청(`test_game_plugin`, `test_game_intake`, `test_game_web`·`test_game_live`의 결합 시험) | 화면 환경 시험은 가짜 Surface로 새로 씀 |

**Surface 계약** (환경이 화면에 요구하는 것, 웹과 Android가 각각 구현):

```text
name, kinds                지원 입력 종류 (keys / tap / drag), reads_dom
capture(region?) → PNG     전체 화면 또는 판 영역
elements(queries) → dict   CSS 선택자로 요소의 class와 자기 글자 읽기 (웹만)
key(key) / tap(x, y, ms) / drag(x0, y0, x1, y1, ms)
```

- 키가 없는 화면(Android)에서 방향 키 조작은 판 영역 안의 스와이프로 바꾼다. 그래서 2048 프로필이 웹과 Android에서 같은 규칙·풀이기로 돈다.
- Android는 화면 판독(픽셀)만 쓴다. 프로필이 없는 게임은 지금처럼 `slide_merge`만 알아보기로 시작할 수 있다.

## 4. 단계

단계마다 바꾼 영역의 시험만 돌리고 커밋한다.

| # | 단계 | 완료 조건 | 크기 |
|---|---|---|---|
| 0 (완료) | 저장소와 환경 | `git init`, `.venv`(3.14), 의존 설치, 이 문서 | S |
| 1 (완료) | 순수 로직과 판독 | 규칙, 풀이기, 계열, 어댑터, 판독 도우미, 세션, replay를 옮김. `test_core`, `test_session`, `test_perception`, `test_sites`, `test_geometry`, `test_discovery` 통과 | M |
| 2 (완료) | 모델 클라이언트와 VLM 질문 | `model.py`, `vision.py`. `test_vision` 통과, 모델 응답 변환 시험 | S |
| 3 (완료) | 화면 환경과 웹 | `screen.py`, `web.py`, `play.py`. 가짜 Surface 시험(측정, 입력, 덮개, 끝 화면, 알아보기)과 로컬 페이지를 Chromium으로 여는 시험 | M |
| 4 (완료, 실기기 미확인) | Android | `android.py`. 가짜 adb 시험(캡처, 탭, 스와이프, 키). 에뮬레이터가 켜져 있으면 캡처·입력 연기 시험 | S |
| 5 (완료) | 평가 CLI | `policies`(replay), `web`(사이트 설정, 정답키, `--perturb`), `android`. 결과 JSON과 마지막 화면 저장 | M |
| 6 (완료) | 마무리 | README, 이 문서 갱신, 전체 시험 1회, 분리 완료 보고 | S |

분리 뒤에는 실험 계획(`docs/origin/`)의 남은 단계를 이어 간다: 3 범용 풀이기 → 4 격리 실행기와 어댑터 생성·채택 → 5 지뢰찾기·블록 → 6 규칙 변형 → 7 평가.
시작은 분리 완료 보고 뒤 사용자와 정한다. 단계 4의 생성 코드는 제한한 subprocess에서 돌린다(환경 변수 비움, 임시 폴더만, 시간·메모리 제한, JSON만 주고받음).

## 5. 측정 방법

| 무엇 | 명령 | 비용 |
|---|---|---|
| 풀이기 실력 (화면 없음) | `python -m gamelab.eval policies --games N` | 없음 (CPU) |
| 세션·판독 회귀 | `pytest` (공유 fixture, 저장 캡처) | 없음 |
| 웹 실사이트 | `python -m gamelab.eval web --site NAME --games N --compute C --out DIR` | 모델 호출, 시간. **사이트와 규모를 정해 사용자 승인 뒤** |
| Android 앱 | `python -m gamelab.eval android --profile ID --package PKG --out DIR` | 같음. 에뮬레이터 `army-game`, 게임 설치는 사용자 |

- 결과: 판마다 `NAME-NN.json`(결과, 요청, 세션 기록, 수별 입력, 모델 답)과 `NAME-NN-final.png`, 실행마다 `summary.json`.
- 지표: 끝 종류와 이유, 확인된 수, 수별 효과(확인·없음·불일치), 오독(정답키가 있는 사이트), 재전송, 한 수 시간, 캡처 수, 모델 질문 수와 토큰, 페이지가 말하는 끝과의 일치.
- `--perturb`: `resize`, `cover`, `cover_bare`, `consent`, `consent_agree`, `tab`, `navigate`, `user`, `cancel`, `filter`. `disconnect`(task 재시작)는 task 저장을 뺐으므로 없다.
- 모델: OpenAI 호환 API. 기본은 Azure 앞의 LiteLLM 프록시(`https://litellm.agenticfw.dev/v1`). 키는 `AGENTIC_OPENAI_KEY`, 모델 이름은 `AGENTIC_OPENAI_MODEL` 또는 `--vision-model`(2026-11-13 사용자 결정으로 OpenRouter에서 바꿈).

## 6. 분리 결과 (2026-11-13)

- 시험 490개 통과: 옮긴 시험 441개(공유 fixture, 세션, 판독, 저장 캡처, 알아보기, VLM 질문)와 새 시험 49개(모델 클라이언트, 화면 환경, Chromium, adb, 평가 CLI).
- 원본과 달라진 동작
  - 스키마 검사가 jsonschema다. 등록 계열과 프로필은 그대로 통과한다.
  - 프로필 주소(`source.url`)가 `https://` 말고 `http://`, `android://`, `file://`도 받는다.
  - 화면 환경은 어댑터가 입력을 만들지 못하면(조각이 사라짐, 화면 밖 드롭) 그 이유로 판을 끝낸다. 원본은 이유 없이 `error`였다.
  - 동의 버튼만 있는 약관 요청은 `--accept-consent`일 때만 누른다. 게임의 계속하기 버튼은 묻지 않고 누른다.
  - 정책 평가에 지뢰찾기가 들어갔다(승률). 원본 도구는 지뢰찾기에서 오류가 났다.
- 확인하지 못한 것
  - **실사이트와 실제 모델 호출.** 웹 평가는 로컬 페이지로만 끝까지 돌렸다. 모델 API 호출은 가짜 응답으로만 시험했다.
  - **실제 Android 기기.** adb 명령은 가짜 adb로만 시험했다. 에뮬레이터 `army-game`은 켜지 않았다.
  - `--scale`(devicePixelRatio)과 `tab`·`navigate`·`filter`·`resize` 교란은 옮겼지만 돌려 보지 않았다.
- 알려진 한계
  - Android에 맞는 등록 프로필이 없다. 지금 프로필은 웹 사이트의 화면 크기와 색에 맞춰져 있다. 프로필 없이 시작할 수 있는 계열은 `slide_merge`뿐이다.
  - Android 드래그는 `input swipe`다. 길게 눌러 집는 게임(블록 배치)에 맞는지는 실기기에서 봐야 한다.
  - 웹 DOM 읽기와 캡처가 한 관측으로 묶이지 않는다(ARMY는 묶었다). 어긋나면 판독이 그 칸을 모름으로 두고 다시 본다.
  - 재시작 뒤 이어 하기(task 저장)가 없다. 세션 기록의 의도·영수증 필드는 남아 있지만 한 프로세스 안에서만 쓴다.

## 7. 분리 뒤 진행

| 날짜 | 한 일 | 기록 |
|---|---|---|
| 2026-11-13 | 모델을 Azure 앞의 LiteLLM 프록시로 바꿈. 실제 호출 확인(`gpt-6.1-sol`) | – |
| 2026-11-13 | 웹 실측 1차: `ovolve_2048`(모델 없음), `march2048_4x4`(프로필 없음, 모델 1회) | [웹 실측 기록](reports/2026-11-13-web-baseline.md) |
| 2026-11-13 | 실험 계획 단계 3: 범용 expectimax와 전용 풀이기 비교 | [단계 3 기록](reports/2026-11-13-step3-generic-solver.md) |
