# game-lab

웹과 Android 게임을 화면만 보고 두는 범용 게임 에이전트 하네스다. 사용자가 말로 맡긴 범위(한 판, 레벨 N개, 이어받기, 이름 붙인 작업)를 끝내고 결과를 돌려준다.
규칙을 아는 계열(2048, 지뢰찾기, 블록 놓기, 3매치, 클론다이크, 떨어지는 블록, 소코반)은 모델이 첫 화면을 보정해 주면 코드가 판을 읽고 풀이기가 수를 고른다.
처음 보는 게임은 모델이 규칙 코드를 쓰거나(만든 계열) 입력을 직접 고르고(직접 두기), 판 밖 화면은 한 번 지난 길을 코드가 다시 누른다(루틴).
긴 게임(덱 빌딩 같은 것)에서는 직접 두기 위에 장치가 얹힌다: 두기 전의 조사(스토어 설명·게임 안 도움말), 카드 싸움의 턴을 코드가 탐색해 두기(카드는 생김새로 가름), 되돌릴 수 없는 선택의 후보와 예상(`pick`), 작업이 끝난 뒤의 복기(교훈을 다음 작업에 줌), 화면의 숫자 읽기.
떨어지는 블록류(실시간: 모델은 우물과 조작을 한 번 말하고, 코드가 초당 몇 번 읽고 계열의 풀이기로 놓을 곳을 고름. 웹과 Android 몸짓 조작), 요청 사이에 이어지는 게임(세계 기억), 스토리형(대사는 코드, 선택지는 모델)도 뼈대가 있다.
데모 페이지(`eval serve`)는 게임 화면 옆의 대화창에서 요청을 받고, 판이 도는 중의 "어디까지 했어?"에 코드가 센 진행으로 답한다.

ARMY 게임 플러그인(`codex/game-adaptive-perception` `92ccefe5f`)에서 분리했다.

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: 요청에서 종료까지의 구조도, 경로 선택 순서, 한 수 루프, 모듈별 역할과 지원 범위(2026-11-24)
- **[docs/STATUS.md](docs/STATUS.md)**: 지금 구조, 경로마다 되는 것과 실측 숫자, 안 되는 것, 다음에 할 일(세션 Z, 2026-11-21. R1·P2·D2를 합친 뒤 고침, 2026-11-23. §36 E0~M11b를 넣음, 세션 Z2, 2026-11-24)
- [docs/PLAN.md](docs/PLAN.md): 계획과 세션 기록(§24~§36)
- [docs/PLAN-archive.md](docs/PLAN-archive.md): 분리 계획과 그 전의 기록(§1~§23)
- [docs/origin/](docs/origin/2026-11-13-game-adaptive-perception.md): 처음 실험 계획과 조사, [docs/reports/](docs/reports/): 단계별 리포트

## 설치

```sh
uv venv --python 3.14 .venv
uv pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
uv pip install -e ".[demo]"      # 데모 페이지(eval serve)를 쓸 때만: aiohttp
```

## 구조

```text
agent.py                    한 판의 루프: 경로를 고르고, 판을 읽고, 수를 보내고, 결과를 규칙 모델로 확인
├─ work.py, contracts.py    요청한 범위만큼 판을 잇고 돌려주기 (GoalSpec: 목표, 범위, 멈춤 조건, 허락한 판 밖 행동, 이야기 방침)
├─ request.py               말로 한 요청을 목표로
├─ adaptation.py            게임 식별, 계열 보정과 채택 검사, 사이트·게임별 보관 (Store)
├─ research.py              두기 전의 조사: 스토어 설명·위키·모델이 아는 것으로 질문 하나 (식별 앞, research.json)
├─ kits/, adapters/kit.py   계열 판독 키트 (블록 놓기, 2048, 지뢰찾기, 3매치, 소코반)
├─ adapters/generated.py    모델이 쓴 판독 코드 (sandbox/에서 실행, 클론다이크)
├─ made.py                  등록 안 된 게임에서 모델이 쓴 규칙·풀이 모듈 (만든 계열)
├─ direct.py                모델 직접 두기: 수 묶음, 웹 요소 글, 스토리형 줄 넘기기와 선택지, 긴 게임(턴 뒤 기다리기, 읽은 글·판 상태 보관), 고르기(pick)
├─ fight.py                 카드 싸움: 판독 질문 하나, 카드를 생김새로 가름, 규칙(가설)을 본 값으로 고쳐 씀, 손패 탐색, 카드 끌기
├─ review.py                작업이 끝난 뒤의 복기: 질문 하나로 교훈을 다시 씀 (memory.json의 lessons·reviews)
├─ lattice.py               계열 없는 격자 판을 글로: 모델이 말한 격자의 칸을 생김새로 묶어 글자 판으로(직접 두기에서 씀)
├─ recovery.py              판을 못 읽는 화면에서 모델이 고른 입력 하나
├─ routine.py               판 밖 화면의 "이 화면이면 이 입력" 보관과 재생
├─ realtime.py              떨어지는 블록류의 실시간 코드 루프 (우물·다음 조각 읽기, 키와 몸짓, 놓았다가 돌아오기)
├─ world.py                 요청 사이에 이어지는 게임의 세계 기억 (world.json)
├─ scenes.py                화면 종류와 맴돌기, 화면에서 읽는 숫자(measures)
├─ samples.py, carry.py     칸의 견본, 끌어 옮기기
├─ frames.py, motion.py     프레임, 움직이는 것을 걸러 본 화면
└─ 계열 (catalog, families)  규칙 모델(rules/) + 풀이기(solvers/) + 기술자(blueprint/families/). 떨어지는 블록(falling)은 키트 없이 실시간 루프가 씀
화면 (surface.py)
├─ environments/web.py      Playwright Chromium (페이지 요소 outline 포함)
└─ environments/android.py  adb (screencap, input tap/swipe/keyevent)
eval/                       평가 CLI (policies, web, android, offline, checks, serve, models)
├─ talk.py                  데모 대화창의 문장: 최초 요청의 답과 끝의 말(코드), 말 가르기·질문의 답(모델)
├─ guard.py                 요청으로 연 페이지가 공개 주소에만 닿게 하는 주소 확인과 프록시
├─ emulator.py              데모가 쓰는 에뮬레이터: 켜져 있으면 쓰고, 없으면 켬
└─ serve.py, serve.html     데모 페이지: 게임 화면과 대화창 (POST /say, 루프의 진행 알림 agent.progress)
```

경로마다 하는 일과 파일은 [STATUS.md §1](docs/STATUS.md)의 표에, 요청에서 종료까지의 흐름과 모듈별 지원 범위는 [ARCHITECTURE.md](docs/ARCHITECTURE.md)에 있다.

안전 장치(승인, 입력 직전 픽셀 재검사, 영수증)는 없다. 화면은 믿고 입력을 보내며, 결과만 규칙 모델로 확인한다. 광고·결제·로그인·외부 링크, 게임의 힌트·자동 버튼, 진행 중인 판을 버리는 새 게임은 누르지 않는다.

## 측정

```sh
# 풀이기 실력 (화면 없음, 비용 없음)
.venv/bin/python -m gamelab.eval policies --games 100 --out out/policies.json
.venv/bin/python -m gamelab.eval policies --family falling --games 1000 --rule goal_lines=2 --rule goal_pieces=9 --hide goal_lines --hide goal_pieces   # 층의 목표, 목표를 모를 때와 견줌
.venv/bin/python -m gamelab.eval policies --family falling --games 500 --rule 'pieces="pentomino"' --rule goal_lines=2 --rule goal_pieces=9   # 4칸이 아닌 조각(pentomino, small)

# 모델 설정은 .env에 둔다 (.env.example 참고). 쓸 수 있는 모델 이름 보기:
.venv/bin/python -m gamelab.eval models

# 웹 실사이트 (모델 비용이 든다). 사이트는 sites.json의 이름으로만 정한다
.venv/bin/python -m gamelab.eval web --site ovolve_2048 --games 3 --objective reach_value --target 2048 --compute 500 --out out/ovolve
# 라이브는 --compute를 계열의 바닥 값(least_compute)까지 올린다: 2048 8000, 지뢰찾기 20000, 블록 놓기 1000
.venv/bin/python -m gamelab.eval web --site march2048_4x4 --identify --games 3 --compute 500 --out out/march   # 계열도 화면에서 찾음

# Android (에뮬레이터를 먼저 켠다: emulator -avd game-lab2)
.venv/bin/python -m gamelab.eval android --check --out out/device        # 연결 확인과 캡처 한 장
.venv/bin/python -m gamelab.eval android --package PKG --hint "어느 게임인지" --compute 500 --out out/app
.venv/bin/python -m gamelab.eval android --ask "퍼즐 앱에서 Fifteen 끝까지" --compute 200 --out out/ask   # 설치된 앱 가운데서 고름
.venv/bin/python -m gamelab.eval android --ask "꿈의 집 하던 판 이어서 레벨 3개 깨줘, 지면 멈춰" --compute 200 --out out/ask   # 범위: 이어받기, 레벨 N개, 멈춤
.venv/bin/python -m gamelab.eval android --package PKG --resume --objective levels --target 3 --allow retry --compute 200 --out out/app
# 긴 게임(직접 두기 + 조사·카드 싸움·고르기·복기): 이야기·지도의 방침은 --choices, 상점에서 쓰는 것은 --allow currency
.venv/bin/python -m gamelab.eval android --package it.sharklab.rogueadventure --identify --accept-consent --resume --objective play_until_end --games 1 --compute 200 --choices "체력을 잃지 않는 안전한 쪽으로" --allow currency --out out/android/rogue

.venv/bin/python -m gamelab.eval offline --vision-model MODEL --out out/offline/MODEL   # 사이트 없이 보관된 화면으로 모델을 잼
.venv/bin/python -m gamelab.eval web --ask "https://… 에서 이 퍼즐을 끝까지 풀어줘" --compute 200 --out out/ask   # 말로 한 요청: 주소와 목표를 거기서 읽음

# 처음 보는 게임에서 방법끼리 견주기: 직접 두기(direct) 또는 만든 계열(made)만으로
.venv/bin/python -m gamelab.eval web --site tatham_singles_seeded --way direct --games 3 --compute 200 --out out/web/singles-direct
# 실시간(떨어지는 블록): 줄 5개. 다음 조각 미리보기와 층의 목표는 준비 답에서 받음(보관 폴더가 새로우면 한 번 물음)
.venv/bin/python -m gamelab.eval web --site tetrjs_falling --identify --objective reach_value --target 5 --compute 200 --max-actions 6000 --out out/web/falling
# 스토리형: 되돌릴 수 없는 선택은 --choices 방침이 있을 때만 누르고, 없으면 그 자리에서 돌려줌
.venv/bin/python -m gamelab.eval web --site monogatari_story --identify --compute 200 --choices "착한 쪽으로" --out out/web/story
# 이름 붙인 작업(루틴과 세계 기억): 두 번째부터는 코드가 다시 누르고, 때가 안 됐으면 남은 시간을 돌려줌
.venv/bin/python -m gamelab.eval web --ask "adarkroom.doublespeakgames.com 게임에서 나무 모으기를 한 번 해줘" --identify --compute 200 --out out/web/errand
```

Android 기기 하나는 한 실행만 움직인다(`~/.gamelab/lock`, 기기마다. 체크아웃마다가 아니라 사용자의 것이라 작업 트리의 세션과 데모가 서로를 본다. `GAMELAB_LOCKS`로 바꿈). 잡혀 있으면 기다리지 않고 거절한다. 웹은 실행마다 브라우저가 따로라 잠그지 않는다.
앱 이름은 APK의 `application-label`을 한 번 읽어 `out/apps/`에 둔다. 잘못 읽히거나 판마다 데이터를 지워야 하는 앱(`pm clear`)은
`src/gamelab/eval/apps.json`에 손으로 적는다(`{"PKG": {"label": "부르는 이름", "clear": true}}`).

게임에 대해 보관하는 것은 `--adapter-store`(기본 `out/adapters`) 아래 사이트(앱)별 폴더에 둔다: 계열별 판독기와 값,
판 사이 화면의 루틴(`routines/`, 모델이 한 번 넘긴 화면을 다음부터 코드가 다시 누름), 모델의 기억(`memory.json`: 규칙·길·판의 상태와 한 번 읽은 카드·적의 설명, 카드 싸움의 규칙·그림·손패 자리 `card_rules`·`card_looks`·`card_places`, 복기의 교훈 `lessons`·`reviews`), 계열 없음(`unfamiliar.json`), 두기 전의 조사(`research.json`),
모델이 만든 계열(`made.json`), 실시간 게임의 준비 답과 판에서 배운 것(`falling.json`: 우물, 조작, 미리보기 상자, 쓸기 감도. 층의 목표는 남기지 않음), 세계 기억(`world.json`). 한 앱에 게임이 여럿이면 요청이 고른 게임 이름(`--ask`가 스스로 읽고, 아니면
`--game NAME`)으로 폴더를 나눈다(`…+mines`). 다시 배우게 하려면 그 폴더를 지우거나 다른 `--adapter-store`를 준다.

옛 명령의 `--generate`는 받기만 한다(이제 늘 어댑터를 쓴다). `--profile`, `--unregistered`, `--loop`는 없앴다.

- 사이트 설정과 정답키: `src/gamelab/eval/sites.json`. 옵션 설명은 각 명령의 `--help`.
- 범위(PLAN §25): `--objective levels --target N`은 레벨 N개를 같은 화면에서 잇고, `--objective until_level --target N`은 화면에 보이는 레벨 번호로 세어 레벨 N까지 깬다(시작 전과 레벨을 깰 때마다 모델이 화면의 번호를 읽고, 이미 지났으면 두지 않음). `--stop`(`lost` 또는 말), `--allow`(`boosters`, `currency`, `continue`, `retry`, 기본은 없음), `--resume`(앱을 다시 띄우지 않고 지금 화면에서)을 받는다. 말로 한 요청은 이것을 말에서 읽는다.
- 허락은 이름으로만 받는다. `--choices`의 말("돈을 쓰는 것은 괜찮다")은 선택의 방침이지 허락이 아니다: 상점에서 사게 하려면 `--allow currency`를 함께 준다.
- 처음 보는 게임의 조사와 작업 뒤의 복기는 eval에서 기본으로 켠다(`--no-research`, `--no-review`로 끔). 조사·복기의 토큰은 판 예산 밖이다.
- 그 밖에: `--task NAME`(이름 붙인 작업), `--game NAME`(한 앱의 여러 게임 가운데 하나), `--choices WORDS`(이야기 선택지의 방침), `--way direct|made`(방법끼리 견주기). 말로 한 요청은 앞의 셋도 말에서 읽는다.
- `--games` 2 이상이면 결과의 `model_tokens`는 실행 전체의 누적이다. 판마다의 숫자는 `work.spent`를 본다.
- 결과: 판마다 `NAME-NN.json`과 `NAME-NN-final.png`, 실행마다 `summary.json`. 결과의 `work`는 돌려주기(끝난 까닭, 깬 레벨, 쓴 것, 멈춘 곳)이고 `told`는 그 한 줄이다. `out/`은 git에 넣지 않는다.

## 데모 페이지

왼쪽에 게임 화면, 오른쪽에 에이전트와의 대화창이 있다. 대화창에 요청을 쓰면 웹 페이지나 이 Mac의 Android 에뮬레이터에서
에이전트가 두고, 그 화면을 실시간으로 본다. 에이전트는 무엇을 어디까지 할지(호스트가 확인한 목표에서 코드가 만든 문장),
식별한 게임과 레벨의 끝, 끝났을 때 돌려주는 것(끝난 까닭, 깬·진 레벨, 쓴 것, 멈춘 화면, 하트·쿨다운, 스토리 선택지)을
대화창에 말한다. 판이 도는 중이나 끝난 뒤에 "어디까지 했어?", "왜 멈췄어?"를 물으면 마지막 화면과 코드가 센 진행으로
모델이 답한다(판은 멈추지 않는다). 도는 중의 새 게임 요청은 시작하지 않고, "멈춰"는 중지와 같다. 대화는 판 사이에도 남고
(200줄, 서버를 다시 켜면 비어 있음) 늦게 들어온 사람도 받는다. 수와 화면 수는 접히는 "활동"에 있다.
주소가 있으면 웹(Chromium), 없으면 설치된 앱 가운데서 고른다.
게임 이름이 없어도 앞 판을 이어 하라는 말("10단계까지 깨줘", "3판 더")은 앞 판의 앱·주소로 시작한다(호스트가 앱 목록과 주소를 다시 확인함).
어디까지 할지를 셀 수 없는 말("다리까지")이나 어느 게임인지 모를 요청은 시작하지 않고 한 번 되묻고(`about: "clarify"`), 다음 답을 원래 요청과 합쳐 다시 묻지 않고 시작한다.
여럿 가운데 하나를 고르는 보상 앞에서는 대화창에 후보와 예상을 말하고 45초까지 답을 기다린다(답이 없으면 에이전트가 고른 것을 보냄). 지도의 길·상점은 묻지 않고 결과에 기록만 남긴다.

데모의 기기는 AVD `game-lab2`의 에뮬레이터다(`--avd`). 세션의 실측도 같은 기기에서 한다: 게임과 진행 기록이 거기에 있다. 한쪽이 판을 돌리는 동안 다른 쪽은 기기 잠금으로 거절되고, 데모는 그동안 쉬는 화면도 찍지 않는다. 번호(`emulator-5554` 등)는
켜진 순서로 정해지므로, 붙어 있는 에뮬레이터마다 `adb -s 번호 emu avd name`으로 AVD 이름을 읽어 고른다. 다른 AVD와
실기기는 쓰지 않고, 그것들이 켜져 있어도 `game-lab2`가 없으면 켠다. `--serial`은 번호로 정할 때만 쓴다(그 번호만 씀).
판이 돌지 않을 때 보는 사람이 있으면 1초마다 기기를 보아, 켜져 있으면 그 화면을 보이고 꺼져 있으면 "에뮬레이터 켜기"
버튼을 보인다(`POST /emulator`). 꺼진 채로 앱 요청이 오면 서버가 켠다. 켤 때 스냅샷·GPU 옵션은 그대로이고, 서버가 켠
것이면 서버를 내릴 때 끈다. 설치된 앱 가운데 `eval/apps.json`에서 `"hide": true`인 앱(`ai.agentic.army`)은 요청을 읽는
모델에게 주는 앱 목록에서 빠진다.

`--touch`로 켜면 링크에서 기기 화면을 눌러 조작할 수 있다(`POST /touch`). 사람만 넘길 수 있는 화면(나이 확인, 로그인,
창 닫기)을 링크에서 넘기려고 둔다. 판이 돌지 않고 기기가 켜져 있을 때만 받는다: 짧게 누르면 탭, 500ms 넘게 누르면 그
길이의 길게 누르기, 끌면 쓸기이고, 화면 아래의 "뒤로"·"홈" 버튼이 그 키를 보낸다. 한 번에 하나만 보내고(앞의 입력이 가는
중이면 429), 다른 실행이 기기 잠금을 잡고 있으면 거절한다. 보낸 입력은 보는 사람 모두의 활동 로그에 "직접 누름"으로 남고,
그 뒤 3초 동안은 쉬는 화면을 0.3초마다 찍는다. 기본은 꺼짐이다: 켜면 링크와 토큰을 가진 사람이 기기에서 무엇이든 할 수
있다(Play 스토어, 설정, 로그인된 계정). `--web-only`에서는 켜지지 않는다.

```sh
.venv/bin/python -m gamelab.eval serve --compute 200
# {"serving": "http://127.0.0.1:8765/?k=토큰", ...}  이 주소를 한 번 열면 토큰은 쿠키에 남고 주소에서 빠진다
```

- 모델은 `.env`의 `AGENTIC_OPENAI_KEY`, `AGENTIC_OPENAI_MODEL`(또는 `--vision-model`)을 쓴다.
- 한 번에 한 판(도는 중의 요청은 거절), 판당 입력 6000번(실시간 게임은 키 하나가 입력 하나라 넉넉히 둠. 실제로 묶는 것은 시간과 토큰)·900초·입력 토큰 30만, 하루 20판(`--max-actions`, `--max-seconds`,
  `--max-input-tokens`, `--games-a-day`). 약관 동의는 기본으로 하지 않는다(`--accept-consent`).
- 대화창에 쓴 말은 하루 200번까지(`--chats-a-day`, `out/serve/chat-날짜.jsonl`로 셈). 말을 가르는 질문은 경량 모델
  (`--light-model`, 기본 `gpt-6-luna`), 질문의 답은 `--vision-model`이 하고, 판의 토큰·예산과 따로 센다.
- 접근 토큰은 켤 때마다 새로 만든다. 고정 주소로 열 때는 `--token` 또는 `.env`의 `GAMELAB_DEMO_TOKEN`으로 정한다
  (24자 이상, `A-Z a-z 0-9 _ -`만. 예: `python -c 'import secrets; print(secrets.token_urlsafe(32))'`).
- 끝점: `GET /`, `GET /ws`(이벤트와 화면), `POST /say`(대화창: 게임 요청·질문·중지로 가름), `POST /ask`(게임 요청만,
  모델로 가르지 않음), `POST /stop`(중지 버튼), `POST /emulator`(켜기 버튼: 켜기 시작하면 202, 이미 켜져 있거나 켜는 중이면 아무것도 하지 않고 200, `--web-only`면 409), `POST /touch`(`--touch`일 때만: 화면 누르기, 보내면 202, 꺼져 있으면 403). 모두 토큰 쿠키가 필요하고, `/ws`와 `POST`는 Origin을 검사한다.
- 결과는 `out/serve/`에 `eval web`·`eval android`와 같은 형식으로 남는다(`web-날짜-시각-01.json`, `app-날짜-시각-01.json`).
- 요청의 주소는 공개 IP로만 풀릴 때 열고, 그 페이지가 여는 연결(리다이렉트, 하위 프레임, WebSocket)은 모두 서버 안의
  프록시가 연결마다 이름을 다시 풀어 확인한다.
- 서버는 `127.0.0.1`에만 열린다. 바깥에서 보려면 Cloudflare 터널을 쓴다. 지금까지는 빠른 터널로 열었다: 로그인 없이
  `https://….trycloudflare.com` 주소가 생기고, 켤 때마다 주소가 바뀐다. 이름 붙은 터널(고정 주소)은 Cloudflare에 도메인이
  있어야 해서 만들지 않았다. 주소가 바뀌어도 접근 토큰은 `--token`이나 `GAMELAB_DEMO_TOKEN`으로 고정할 수 있다.
  터널은 Host를 그대로 넘기므로 Origin 검사에 따로 설정할 것이 없다. 데모할 때만 서버와 터널을 켠다.

```sh
cloudflared tunnel --url http://127.0.0.1:8765    # 빠른 터널: 출력에 trycloudflare.com 주소가 나옴
```

## 시험

```sh
.venv/bin/python -m pytest                      # 전체 879개, 약 15분 (로컬 Chromium 포함)
.venv/bin/python -m pytest tests/test_core.py   # 바꾼 영역만
```
