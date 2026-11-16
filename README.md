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
uv pip install -e ".[demo]"      # 데모 페이지(eval serve)를 쓸 때만: aiohttp
```

## 구조

```text
agent.py                    한 판의 루프: 모델이나 어댑터가 판을 읽고, 풀이기가 수를 고르고, 결과를 규칙 모델로 확인
├─ adaptation.py            첫 수 탐색, 어댑터 작성과 채택 검사, 사이트별 보관 (Store)
├─ adapters/generated.py    모델이 쓴 어댑터 코드 (sandbox/에서 실행)
├─ recovery.py              판을 못 읽는 화면에서 모델이 고른 입력 하나
├─ samples.py, carry.py     어댑터가 맡긴 칸의 견본, 끌어 옮기기
├─ frames.py, motion.py     찍힌 시각이 붙은 프레임(지금은 캡처 한 장씩), 계속 움직이는 것을 걸러 본 화면
└─ 계열 (catalog, families)  규칙 모델(rules/) + 풀이기(solvers/) + 기술자(blueprint/families/)
화면 (surface.py)
├─ environments/web.py      Playwright Chromium
└─ environments/android.py  adb (screencap, input tap/swipe/keyevent)
eval/                       평가 CLI (policies, web, android, offline, serve)
├─ guard.py                 요청으로 연 페이지가 공개 주소에만 닿게 하는 주소 확인과 프록시
├─ emulator.py              데모가 쓰는 에뮬레이터: 켜져 있으면 쓰고, 없으면 켬
└─ serve.py, serve.html     말로 요청하는 데모 페이지
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
.venv/bin/python -m gamelab.eval android --package PKG --hint "어느 게임인지" --compute 500 --out out/app
.venv/bin/python -m gamelab.eval android --ask "퍼즐 앱에서 Fifteen 끝까지" --compute 200 --out out/ask   # 설치된 앱 가운데서 고름

.venv/bin/python -m gamelab.eval offline --vision-model MODEL --out out/offline/MODEL   # 사이트 없이 보관된 화면으로 모델을 잼
.venv/bin/python -m gamelab.eval web --ask "https://… 에서 이 퍼즐을 끝까지 풀어줘" --compute 200 --out out/ask   # 말로 한 요청: 주소와 목표를 거기서 읽음
```

Android 기기 하나는 한 실행만 움직인다(`out/.lock`, 기기마다). 잡혀 있으면 기다리지 않고 거절한다. 웹은 실행마다 브라우저가 따로라 잠그지 않는다.
앱 이름은 APK의 `application-label`을 한 번 읽어 `out/apps/`에 둔다. 잘못 읽히거나 판마다 데이터를 지워야 하는 앱(`pm clear`)은
`src/gamelab/eval/apps.json`에 손으로 적는다(`{"PKG": {"label": "부르는 이름", "clear": true}}`).

옛 명령의 `--generate`는 받기만 한다(이제 늘 어댑터를 쓴다). `--profile`, `--unregistered`, `--loop`는 없앴다.

- 사이트 설정과 정답키: `src/gamelab/eval/sites.json`. 옵션 설명은 각 명령의 `--help`.
- 결과: 판마다 `NAME-NN.json`과 `NAME-NN-final.png`, 실행마다 `summary.json`. `out/`은 git에 넣지 않는다.

## 데모 페이지

입력 창에 요청을 쓰면 웹 페이지나 이 Mac의 Android 에뮬레이터에서 에이전트가 두고, 그 화면과 로그를 실시간으로 본다.
주소가 있으면 웹(Chromium), 없으면 설치된 앱 가운데서 고른다. 에뮬레이터가 꺼져 있으면 `army-game`을 켜고(스냅샷·GPU 옵션은 그대로),
서버가 켠 것이면 서버를 내릴 때 끈다.

```sh
.venv/bin/python -m gamelab.eval serve --compute 200
# {"serving": "http://127.0.0.1:8765/?k=토큰", ...}  이 주소를 한 번 열면 토큰은 쿠키에 남고 주소에서 빠진다
```

- 모델은 `.env`의 `AGENTIC_OPENAI_KEY`, `AGENTIC_OPENAI_MODEL`(또는 `--vision-model`)을 쓴다.
- 한 번에 한 판(도는 중의 요청은 거절), 판당 수 300·900초·입력 토큰 30만, 하루 20판(`--max-actions`, `--max-seconds`,
  `--max-input-tokens`, `--games-a-day`). 약관 동의는 기본으로 하지 않는다(`--accept-consent`).
- 결과는 `out/serve/`에 `eval web`·`eval android`와 같은 형식으로 남는다(`web-날짜-시각-01.json`, `app-날짜-시각-01.json`).
- 요청의 주소는 공개 IP로만 풀릴 때 열고, 그 페이지가 여는 연결(리다이렉트, 하위 프레임, WebSocket)은 모두 서버 안의
  프록시가 연결마다 이름을 다시 풀어 확인한다.
- 서버는 `127.0.0.1`에만 열린다. 바깥에서 보려면 Cloudflare 터널을 쓴다(이름 붙은 터널 `game-lab-demo`를 만들고 로그인한 뒤,
  켜기 전에 확인한다). 터널은 Host를 그대로 넘기므로 Origin 검사에 따로 설정할 것이 없다. 데모할 때만 서버와 터널을 켠다.

```sh
cloudflared tunnel run --url http://127.0.0.1:8765 game-lab-demo
```

## 시험

```sh
.venv/bin/python -m pytest                      # 전체 (약 3분, 로컬 Chromium 포함)
.venv/bin/python -m pytest tests/test_core.py   # 바꾼 영역만
```
