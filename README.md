# game-lab

웹과 Android의 정적 게임(2048류, 지뢰찾기, 블록 배치)을 화면만 보고 두는 독립 게임 에이전트 하네스다.
규칙 모델과 풀이기는 코드로 등록해 두고, 사이트마다 다른 판독은 사이트 어댑터가 맡는다. 모델(VLM)은 코드가 읽지 못한 화면에서만 묻는다.

ARMY 게임 플러그인(`codex/game-adaptive-perception` `92ccefe5f`)에서 분리했다. 분리 계획과 버린 것은 [docs/PLAN.md](docs/PLAN.md),
이어 갈 실험 계획은 [docs/origin/](docs/origin/2026-11-13-game-adaptive-perception.md)에 있다.

## 설치

```sh
uv venv --python 3.14 .venv
uv pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
```

## 구조

```text
세션 (session.py)           관측 → 추정 → 계획 → 입력 → 결과 확인. 수 뒤 화면을 규칙 모델의 예상과 대조
└─ 계열 (families, catalog)  규칙 모델(rules/) + 풀이기(solvers/)
환경 (environments/)
├─ replay.py                시뮬레이션 게임 (시험, 정책 평가)
└─ screen.py                실제 화면: 사이트 어댑터(adapters/)로 판을 재고 읽고, 못 읽는 화면은 모델에 물음
   ├─ web.py                Playwright Chromium
   └─ android.py            adb (screencap, input tap/swipe/keyevent)
play.py                     한 판 실행
eval/                       평가 CLI
```

안전 장치(승인, 입력 직전 픽셀 재검사, 영수증)는 없다. 화면은 믿고 입력을 보내며, 결과만 규칙 모델로 확인한다.

## 측정

```sh
# 풀이기 실력 (화면 없음, 비용 없음)
.venv/bin/python -m gamelab.eval policies --games 100 --out out/policies.json

# 모델 설정은 .env에 둔다 (.env.example 참고). 쓸 수 있는 모델 이름 보기:
.venv/bin/python -m gamelab.eval models

# 웹 실사이트 (모델 비용이 든다. --vision-model '' 이면 모델에 묻지 않음)
.venv/bin/python -m gamelab.eval web --profile ovolve_2048 --games 3 --objective reach_value --target 2048 --compute 500 --out out/ovolve
.venv/bin/python -m gamelab.eval web --site march2048_4x4 --games 3 --compute 500 --out out/march   # 프로필 없는 사이트

# Android (에뮬레이터를 먼저 켠다: emulator -avd army-game)
.venv/bin/python -m gamelab.eval android --check --out out/device        # 연결 확인과 캡처 한 장
.venv/bin/python -m gamelab.eval android --family slide_merge --package PKG --compute 500 --out out/app
```

- 사이트 설정과 정답키: `src/gamelab/eval/sites.json`. 옵션 설명은 각 명령의 `--help`.
- 결과: 판마다 `NAME-NN.json`과 `NAME-NN-final.png`, 실행마다 `summary.json`. `out/`은 git에 넣지 않는다.

## 시험

```sh
.venv/bin/python -m pytest                      # 전체 (약 1분, 로컬 Chromium 포함)
.venv/bin/python -m pytest tests/test_core.py   # 바꾼 영역만
```
