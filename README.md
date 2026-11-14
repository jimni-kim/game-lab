# game-lab

웹과 Android의 정적 게임(2048류, 지뢰찾기, 블록 배치, 3매치, 클론다이크)을 화면만 보고 두는 독립 게임 에이전트 하네스다.
규칙 모델과 풀이기는 코드로 등록해 두고, 사이트마다 다른 판독은 모델(VLM)이 그 사이트에 맞춰 쓰는 어댑터 코드가 맡는다.
모델은 첫 수들을 읽고 어댑터를 쓰며, 어댑터가 읽지 못하는 화면을 넘기는 데에만 다시 묻는다.

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
agent.py                    한 판의 루프: 모델이나 어댑터가 판을 읽고, 풀이기가 수를 고르고, 결과를 규칙 모델로 확인
├─ adaptation.py            첫 수 탐색, 어댑터 작성과 채택 검사, 사이트별 보관 (Store)
├─ adapters/generated.py    모델이 쓴 어댑터 코드 (sandbox/에서 실행)
├─ recovery.py              판을 못 읽는 화면에서 모델이 고른 입력 하나
├─ samples.py, carry.py     어댑터가 맡긴 칸의 견본, 끌어 옮기기
└─ 계열 (catalog, families)  규칙 모델(rules/) + 풀이기(solvers/) + 기술자(blueprint/families/)
화면 (surface.py)
├─ environments/web.py      Playwright Chromium
└─ environments/android.py  adb (screencap, input tap/swipe/keyevent)
eval/                       평가 CLI (policies, web, android)
```

안전 장치(승인, 입력 직전 픽셀 재검사, 영수증)는 없다. 화면은 믿고 입력을 보내며, 결과만 규칙 모델로 확인한다.

## 측정

```sh
# 풀이기 실력 (화면 없음, 비용 없음)
.venv/bin/python -m gamelab.eval policies --games 100 --out out/policies.json

# 모델 설정은 .env에 둔다 (.env.example 참고). 쓸 수 있는 모델 이름 보기:
.venv/bin/python -m gamelab.eval models

# 웹 실사이트 (모델 비용이 든다). 사이트는 sites.json의 이름으로만 정한다
.venv/bin/python -m gamelab.eval web --site ovolve_2048 --games 3 --objective reach_value --target 2048 --compute 500 --out out/ovolve
.venv/bin/python -m gamelab.eval web --site march2048_4x4 --identify --games 3 --compute 500 --out out/march   # 계열도 화면에서 찾음

# Android (에뮬레이터를 먼저 켠다: emulator -avd army-game)
.venv/bin/python -m gamelab.eval android --check --out out/device        # 연결 확인과 캡처 한 장
.venv/bin/python -m gamelab.eval android --family slide_merge --package PKG --compute 500 --out out/app

.venv/bin/python -m gamelab.eval offline --vision-model MODEL --out out/offline/MODEL   # 사이트 없이 보관된 화면으로 모델을 잼
```

옛 명령의 `--generate`는 받기만 한다(이제 늘 어댑터를 쓴다). `--profile`, `--unregistered`, `--loop`는 없앴다.

- 사이트 설정과 정답키: `src/gamelab/eval/sites.json`. 옵션 설명은 각 명령의 `--help`.
- 결과: 판마다 `NAME-NN.json`과 `NAME-NN-final.png`, 실행마다 `summary.json`. `out/`은 git에 넣지 않는다.

## 시험

```sh
.venv/bin/python -m pytest                      # 전체 (약 3분, 로컬 Chromium 포함)
.venv/bin/python -m pytest tests/test_core.py   # 바꾼 영역만
```
