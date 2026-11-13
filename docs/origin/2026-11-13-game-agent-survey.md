# 게임 에이전트 조사: 판독 적응 실험에 가져올 것

작성: 2026-11-13. 대상 계획: [게임 판독 적응 실험 계획](../plans/2026-11-13-game-adaptive-perception.md) (이하 "실험 계획").
조사 방법: 저장소는 직접 받아 코드 확인, 논문은 arXiv 본문 확인. 출처마다 커밋·절을 적음. **확인**은 원문에서 본 사실, **추론**은 이 문서의 판단, **미확인**은 확인하지 못한 것.

## 요약

**가져올 것 다섯 가지**

| # | 가져올 것 | 출처 | 실험 계획의 자리 |
|---|---|---|---|
| 1 | **기록한 전이 하나하나를 참·거짓 시험으로 만들고, 실패한 시험의 차이를 되먹여 고치는 반복.** 후보 여럿을 나무로 두고 고를 후보를 정함. 기록을 학습용과 확인용으로 나누고, 플레이 중 "온라인 정확도"를 따로 잼 | Code World Models for GGP, GIF-MCTS, WorldCoder | §5 순서 3·4(어댑터 생성, 채택 시험), §8 단계 4·6 |
| 2 | **매 턴 모델에게 이미지 대신(또는 이미지와 함께) 코드가 읽은 상태 표와 규칙 모델의 합법 행동 목록을 주고, 목록의 id 하나만 고르게 함.** 고른 행동은 규칙 모델이 다시 검사 | lmgame-Bench 지각 모듈, BALROG, CWM for GGP(LLM 직접 플레이의 위법 수) | §5 순서 2(첫 몇 수), 순서 6의 매 턴 VLM 바닥 |
| 3 | **판 영역 이미지에 번호 붙은 격자·좌표 덧그림을 얹고, 답을 픽셀이 아니라 칸 번호로 받음** | Claude Plays Pokémon(원본 + 좌표 덧그림 두 장), Gemini Plays Pokémon, VideoGameBench, Set-of-Mark, GamingAgent `draw_grid_on_image` | §5 순서 2, 순서 4의 ④ VLM 교차 확인, 어댑터 생성 입력 |
| 4 | **"반성(reflection)"을 모델 호출이 아니라 코드가 만든 예측·관측 차이 한 줄로.** 최근 N수의 (상태, 행동, 효과) 짧은 기록을 다음 판단과 어댑터 생성의 입력으로 씀 | GamingAgent 기억 모듈, lmgame-Bench 부록 F.1, Cradle 자기 반성 | §5 순서 2의 (관측, 행동, 관측) 기록, 순서 3 생성 프롬프트의 맥락 데이터 |
| 5 | **평가 방식:** 무작위와 기준(손으로 쓴 어댑터) 사이로 정규화한 점수, 무작위 대비 효과 크기, 짝지은 비교, 전이 정확도(학습·확인·온라인), 모델 호출 수, 위법·무효 출력 비율, 2048의 "10턴 무변화면 끝" 규칙 | GIF-MCTS, lmgame-Bench, CWM for GGP, BALROG | §9 지표, §8 단계 7 |

**실험 계획 일부에 반하는 발견**
- **첫 몇 수를 모델이 화면만 보고 두는 단계(§5 순서 2)가 가장 약한 고리.** 공개 결과에서 VLM의 화면 판독과 드래그는 약함(VideoGameBench 드래그 연습 게임 0~10%, BALROG 이미지 추가 시 일부 모델 성적 하락). lmgame-Bench 하네스의 판 게임 이득도 이미지 판독이 아니라 env 내부 상태의 글자 표에서 나옴. 그래서 "모델이 읽고 행동의 입력 위치를 짚고, 고르는 것은 코드"로 바꾸는 안을 아래에 제안.
- **처음 k수의 전이만으로 하는 채택 시험(§5 순서 4 ②)은 드문 전이와 끝 장면을 놓침.** GIF-MCTS는 CWM 정확도가 늘 보상보다 높았고 그 원인으로 버퍼에 드문 종료 상태를 꼽음. CWM for GGP의 Gin rummy는 학습 0.78, 확인 0.75에서 멈춤. 플레이 중 계속하는 효과 확인(지금 세션)이 사실상의 "온라인 시험"이라 그것을 지표로 남기는 안을 제안.
- **"②만 실패하고 ④가 맞으면 규칙이 다름"이라는 판정은 한 화면 교차 확인으로는 모호.** 같은 방향으로 일관되게 틀리는 어댑터(값 표 하나가 밀림)도 같은 신호를 냄. 후보 여럿(어댑터 × 규칙 변형)을 두고 모든 기록을 설명하는 가장 작은 변경을 고르는 안을 제안.
- **발표된 코드 세계 모델 성공 사례는 모두 상태를 환경에서 기호로 받음**(OpenSpiel 문자열, gym, 손으로 고친 OCAtari, Mineflayer API, 에뮬레이터 RAM). 사이트마다 판독 코드를 생성해 맞추는 공개 사례는 찾지 못함. 이 실험의 어댑터 생성은 참고할 성공률이 없는 새 위험(추론).

## 조사 대상별 정리

### GamingAgent / lmgame-Bench

**무엇:** UCSD 등의 게임 벤치마크와 하네스. 2048·Sokoban·Tetris·Candy Crush·Super Mario·Ace Attorney 등을 gym 스타일 env로 돌리고, 지각·기억·추론 모듈을 켜고 끄며 비교. 저장소 `lmgame-org/GamingAgent` 커밋 `996d848` (2025-09-11), MIT (Copyright 2025 LM Games). 논문 arXiv 2505.15146v2.

**확인**
- 한 턴의 흐름: `BaseAgent.get_action` (`gamingagent/agents/base_agent.py:361`)이 하네스 모드면 지각 → 기억 → 추론을 차례로 부름. 지각(`perception_module.py:107`)은 이미지 확대 후 선택적 덧그림(`scaffolding`: 예 `draw_grid_on_image`, `tools/utils.py:56`)을 하고 VLM에 설명을 받음. 기억(`memory_module.py:168`)은 최근 N턴 기록에 대해 LLM이 100단어 이하 반성을 씀(`_reflect`, `:93`). 추론(`reasoning_module.py:69`)이 상태·기억·반성을 템플릿에 채워 `thought:` / `move:` 형식 답을 받고 정규식으로 파싱(`:201`).
- 즉 하네스를 다 켜면 **한 수에 모델 호출이 최대 세 번**(지각, 반성, 추론). 기록이 `max_memory`(2048 설정 10)를 넘으면 요약 호출이 더해짐(`_summarize`, `:121`).
- 2048 설정(`gamingagent/configs/custom_01_2048/config.yaml`)은 `observation_mode: "text"`. 논문 §2.2.1도 판 게임의 지각 모듈은 "게임 백엔드에서" 판을 글자 표로 바꾼다고 씀. **판 게임에서 하네스의 이득은 이미지 판독이 아니라 정답 상태 주입에서 나옴.**
- 측정된 효과(논문 부록 E.2, 모델 10개 짝지은 t검정): Candy Crush +224.8%, Sokoban +537.5%, 2048 +22.4% (p=0.042), Tetris +27.1%, Ace Attorney +123.1%, Super Mario +19.3%(유의하지 않음). 하네스 없이는 텍스트 전용 모델을 빼고 40%의 실행이 무작위를 못 넘었고, 하네스로 86.7%가 넘음(§2.2.1).
- 2048 지표: 합친 타일 값의 합, 10턴 연속 판이 안 바뀌면 끝, 점수 = 10 × log2(합)(부록 G). 무변화 판정은 `gym_env_adapter.py:256` `verify_termination`(관측 해시).
- 무효 행동: `map_agent_action_to_env_action`(`gym_env_adapter.py:320`)이 못 알아본 문자열을 `None`(건너뜀)으로, 2048 env는 그 턴을 진행하지 않음(`twentyFortyEightEnv.py:300`).
- 반성의 질(부록 F.1): o3의 반성은 어느 타일이 합쳐지고 빈칸이 몇 개 늘었는지를 정확히 셈. GPT-4o는 "효과 적음" 수준.
- 실화면 경로 `computer_use/games/*`는 pyautogui 전체 화면 캡처와 OS 입력. Tetris·Mario 작업자는 **모델이 쓴 pyautogui 코드를 `exec()`로 바로 실행**(`computer_use/games/tetris/workers.py:482`, `superMario/workers.py:85`).
- `reasoning_module.py:89-90`에서 `use_memory`가 `self.use_perception`에, `use_perception`이 `self.use_memory`에 묶여 있음(두 깃발이 서로 바뀐 모양). 그 영향으로 기억·지각 단독 끄기 실험이 섞였을 수 있음(추론).
- 의존성: `numpy==1.24.4` 고정, opencv 두 벌, gym·gymnasium·stable-retro·pyboy·vizdoom(`requirements.txt`).

**가져올 것**
- (a) 매 턴 판단의 틀: 규칙 요약 + 상태 표 + 최근 기록 + 한 줄 형식의 답. 다만 상태 표는 ARMY 어댑터(또는 그 부분 판독, 못 읽은 칸 `null`)가 만들고, 답은 자유 문자열이 아니라 규칙 모델 `legal_actions`의 id 중 하나.
- (a) 반성 대신 코드가 계산한 차이: "합쳐진 타일, 빈칸 수 변화, 예측과 다른 칸". o3 반성이 잘한 일을 코드가 정확하고 공짜로 함(추론).
- (b) `draw_grid_on_image` 같은 덧그림을 판 영역에만(전체 화면 아님).
- (e) 2048 점수식과 무변화 끝 규칙, 무작위 대비 효과 크기(Glass's δ)와 짝지은 비교.

**가져오지 않을 것**
- 한 수 세 번 호출 구조를 주 경로로. 실험 계획 비목표와 같음.
- 모델 코드 `exec()` 입력 경로. "에이전트는 제안만, Sentinel이 결정"과 정면 충돌.
- `APIManager`, 모듈 클래스, 의존성 묶음. 아이디어만 가져오고 코드는 반입하지 않음. 상위 계획 §9.2의 "행동 파서 반입"도 ARMY 행동이 규칙 모델 id 목록이면 필요 없음(추론).
- 무효 답을 건너뛰고 계속 가는 처리. ARMY는 무효 답이면 다시 묻거나 멈춤.

### Code World Models for General Game Playing (Lehrach 외, Google DeepMind, 2025)

**무엇:** LLM이 게임 규칙 글과 기록 5판으로 Python 세계 모델(상태, 합법 수, 전이, 관측, 보상, 종료)을 쓰고, 그 위에서 MCTS / ISMCTS가 둠. 숨은 정보 게임에는 숨은 상태를 추정하는 "추론 함수", 탐색용 가치 함수도 생성. arXiv 2510.04542v1, CC BY 4.0. 코드 공개 여부 미확인.

**확인**
- 채택·개선 방식(§4.1): 기록의 **전이 하나마다 단위 시험**(상태, 관측, 보상, 행동 합법성, 실행 오류 없음)을 자동 생성. 시험은 참·거짓이라 "전이 정확도" = 통과 비율. 1.0이 되거나 예산이 다할 때까지 고침. 실패 시험의 스택 트레이스를 되먹임.
- 개선 두 방식: 대화형(순차)과 나무 탐색. 나무 탐색은 후보 CWM 여럿을 두고 Thompson sampling으로 다음에 고칠 후보를 고름(정확도 높거나 덜 고친 것 우선). 매 호출은 새 프롬프트(고를 후보 + 지시 + 실패 시험 하나). 되돌아갈 수 있어 어려운 경우에 강하다며 나무 탐색을 기본으로 씀(§5.1.1).
- 무작위 플레이 몇 번을 시험에 더해 실행 오류가 없는지 봄(§4.2).
- 정확도를 학습·확인(test)·온라인으로 나눠 보고(표 1). 완전 정보 게임 다섯은 모두 정답 CWM. 숨은 정보: Bargaining·Leduc·Quadranto는 1.0 근처, Hand of war는 확인 0.98에 LLM 호출 144회, **Gin rummy는 500회에도 학습 0.78 / 확인 0.75**(다단계 점수 계산).
- 가치 함수는 정답이 없어 고치지 않고, 여럿을 만들어 토너먼트로 고름(§4.3).
- LLM이 직접 두는 기준선(Gemini 2.5 Pro)은 위법 수·예외로 기권이 많음. Backgammon에서 CWM-MCTS의 승리 100/100이 상대 기권(부록 표 7). 10게임 중 9게임에서 CWM 쪽이 같거나 나음.
- 상태는 OpenSpiel이 줌. 화면 판독 없음.

**가져올 것**
- (c) 채택 시험 ②를 "전이 단위 시험 목록 + 통과율"로. 실패한 전이 하나(이전 상태, 행동, 관측 상태, 규칙 모델의 예측, 차이)를 생성 프롬프트에 되먹임. 후보 여럿을 두고 고르는 나무.
- (c) 기록을 학습용과 확인용으로 나눔(예: 마지막 몇 전이는 생성에 보이지 않고 채택에만 씀). 플레이 중 효과 확인 결과를 "온라인 전이 정확도"로 기록.
- (c) 생성 규칙 모델에 무작위 플레이 시험(실행 오류, 끝나는지)을 더함. 실험 계획 단계 6에 해당.
- (e) 지표: 전이 정확도(학습·확인·온라인), 수렴까지 LLM 호출 수.
- `evaluate` 생성이 필요할 때(실험 계획 §3 표) 정답이 없으니 여러 개를 만들어 replay 토너먼트로 고르는 방식.

**가져오지 않을 것**
- 규칙 글 전체에서 처음부터 규칙을 쓰는 것. ARMY는 등록 규칙 모델이 있어 "등록 모델의 변형"이 더 작은 문제(추론).

### GIF-MCTS / Code World Models Benchmark (Dainese 외, NeurIPS 2024)

**무엇:** 환경 설명 + 오프라인 기록으로 `step()`을 가진 Python 환경 클래스를 생성. 생성(generate)·개선(improve)·고치기(fix) 세 행동을 MCTS로 고름. arXiv 2405.15383, 코드 `nicoladainese96/code-world-models`(라이선스 미확인).

**확인**
- 고치기 행동: 문법·실행 오류로 점수 0이 된 프로그램도 논리는 맞을 수 있어 오류만 고치는 행동을 따로 둠.
- 결과(표 2): Llama 3 70B 50회 예산에서 이산 행동 환경 정확도 0.84, 정규화 보상 0.76. GPT-4 Turbo 10회 0.91 / 0.81. 연속 행동은 크게 낮음. 재구현한 WorldCoder보다 높음.
- **정확도-보상 차이:** CWM 정확도는 늘 정규화 보상보다 높고, 둘은 CWM이 완벽할 때만 같음. 주된 원인은 **종료 상태를 틀리게 예측**하는 것. 성공 종료 상태가 버퍼에 드묾.
- 정규화 보상: 무작위 = 0, 정답 모델을 쓴 계획기 = 1.

**가져올 것**
- (c) "고치기"를 따로: 어댑터·규칙 변형이 예외·시간 초과·잘못된 JSON이면 같은 코드를 오류만 고치게 한 번 더 부름. 처음부터 다시 쓰는 것보다 쌈(추론).
- (c) 끝 장면(`won`, `over`)은 처음 k수에 거의 안 나오므로 채택 시험이 보지 못함. 별도 근거(저장된 끝 장면 캡처, 장면 모르면 지금처럼 VLM에 물음)를 둠.
- (e) 정규화 점수: (생성 어댑터 점수 − 무작위) / (기준 어댑터 점수 − 무작위).

### WorldCoder (Tang, Key, Ellis, NeurIPS 2024)

**무엇:** 상호작용 기록을 설명하는 Python 전이·보상 함수를 쓰고 그 위에서 계획. arXiv 2402.12275v3, CC BY-NC-SA 4.0(논문). 코드 라이선스 미확인.

**확인**
- 기록의 일부로 후보를 쓰게 하고, 맞지 않는 전이를 되먹여 고침(§2.2). 고칠 후보는 REx(밴딧)로 고름.
- "불확실할 때 낙관": 기록을 설명할 뿐 아니라, 그 모델로 계획하면 목표에 닿을 수 있어야 함. 이 조건이 탐색이 어려운 환경의 표본 효율을 크게 올림(부록 A).
- 규칙을 바꾼 Sokoban(순간이동 문, 상자가 막으면 꺼짐)도 배움. 지식 이전은 코드 편집으로.
- 한계(§5): 결정적 동역학만, 상태는 기호·이산이어야 함.

**가져올 것**
- (c) 규칙 변형을 "등록 모델의 코드 편집"으로. WorldCoder의 이전 실험이 그 근거.
- (c) 낙관 조건의 ARMY판: 채택한 규칙 변형에서 `legal_actions`가 비지 않고 풀이기가 끝까지 수를 낼 수 있는지 replay로 확인(추론).

**가져오지 않을 것:** 결정적 가정. 2048·블록은 무작위라 `outcomes` 집합 소속 검사가 필요(실험 계획 §4.1에 이미 있음).

### PoE-World (Piriyakulkij 외, 2025), WALL-E (Zhou 외, 2024)

**무엇:** PoE-World는 작은 프로그램 "전문가" 여럿의 가중 곱으로 세계 모델을 만듦(Pong, Montezuma's Revenge, 1000프레임 미만 시연). WALL-E는 LLM 세계 모델의 예측과 실제 전이가 어긋난 곳에서 규칙을 뽑고 코드로 옮겨 덧붙임(Minecraft, ALFWorld). arXiv 2505.10819, 2410.07484.

**확인**
- PoE-World: 상태는 물체 중심(경계 상자, 속도). 물체 검출은 **손으로 고친 OCAtari**에 맡김. 관측과 어긋나거나 1% 미만만 설명하는 제약은 버림.
- WALL-E: 예측·실제 비교 → 규칙 유도 → 코드 규칙 → 맞는 전이만 덮는 규칙과 중복 규칙 제거. 한계로 무작위성을 다루지 못함을 적음.

**가져올 것**
- (c) 규칙 변형을 통째 다시 쓰지 말고 등록 모델 위의 작은 덧붙임(조건 하나, 규칙 값 하나)으로. 먼저 규칙 값(`DEFAULT_RULES` 범위) 변경, 그다음 코드 변경 순.
- (c) 덧붙인 조각이 실제로 실패한 전이를 설명하는지, 맞던 전이를 깨지 않는지 따로 봄.

### Voyager (Wang 외, 2023)

**무엇:** Minecraft에서 GPT-4가 Mineflayer API 코드로 기술을 쓰고 기술 라이브러리에 쌓음. arXiv 2305.16291.

**확인**
- 매 생성 뒤 실행해 환경 피드백과 실행 오류를 다음 프롬프트에 넣음. 4회 생성까지 실패하면 다른 과제로.
- 자기 검증(다른 GPT-4가 비평가로 성공 여부 판단)을 빼면 발견 물건 수가 가장 크게 떨어짐(절제 실험).
- 상태와 행동은 게임 API로 받음. 화면 판독 없음.

**가져올 것:** (b)(c) 생성 반복 횟수 상한(여기서는 4회)과 실패 시 다른 경로로 넘어가는 규칙. 저장 어댑터를 사이트별로 다시 쓰는 것(실험 계획 §4.4)이 기술 라이브러리와 같은 모양.

**가져오지 않을 것:** 성공 판정을 LLM 비평가에 맡기는 것. ARMY는 규칙 모델과 효과 확인이 판정.

### Cradle (Tan 외, 2024)

**무엇:** 스크린샷 입력, 키보드·마우스 출력의 범용 컴퓨터 제어(RDR2, Stardew Valley, Cities: Skylines 등). 정보 수집, 자기 반성, 과제 추론, 기술 정리, 행동 계획, 기억의 6모듈. arXiv 2403.03186.

**확인**
- 정보 수집은 GPT-4o OCR·시각 이해에 템플릿 매칭, Grounding DINO, SAM을 더하고, 축 그리기 같은 시각 프롬프트를 씀.
- 기술 정리: 모델이 `io_env.key_press`, `io_env.mouse_click` 예시를 보고 기술 코드를 생성해 절차 기억에 저장. 툴바 항목은 SAM으로 찾고 마우스를 올려 설명을 읽어 기술을 만듦.
- GPT-4o·GPT-4V는 정밀한 판단에 경계 상자 같은 보조 단서가 필요했고, 게임 고유 아이콘을 잘 못 알아봄.

**가져올 것:** (b) 템플릿 매칭(같은 사이트의 칸 그림 표본을 기록에서 잘라 두고 맞춤). 판독 도우미 라이브러리 후보.

**가져오지 않을 것:** 생성 기술 코드가 입력 API를 직접 부르는 구조. 실험 계획 §6("생성 코드는 입력 도구를 받지 않음")과 충돌. 실시간 3D용 무거운 검출 모델.

### VideoGameBench (Zhang 외, Princeton, 2025)

**무엇:** 1990년대 게임 23개를 원 화면만으로. 일시 정지형 Lite판, 연습 게임 셋. arXiv 2505.18134v3.

**확인**
- 최고 모델(Gemini 2.5 Pro, Claude Sonnet 3.7)이 전체의 0.48%, Lite 1.6%.
- 연습 게임(표 4, 250행동에 목표 10개): 위치 클릭 Sonnet 3.7·Gemini 2.5 Pro 100%, **마우스 드래그 최고 10%**, 2D 길찾기 Sonnet 3.7 80%.
- 마우스 게임 프롬프트는 100px 간격 좌표 격자와 좌표 라벨을 덧그림.
- 진행 측정: 공략 영상에서 뽑은 지점 화면의 이미지 해시와 현재 화면 비교.
- 실패 양상: 아는 것과 하는 것의 차이, 화면 오독, 메모장 덮어쓰기로 기억 유실.

**가져올 것**
- (a)(d) 드래그는 모델에게 시키지 않음. 모델은 칸 번호만, 드래그 좌표와 집는 위치 보정은 코드(지금 블록 계열과 같음).
- (e) 클릭·드래그 연습판 같은 합성 페이지로 어댑터의 `targets`를 따로 시험하는 것(지금 합성 브라우저 시험의 확장).

### BALROG (Paglieri 외, 2024)

**무엇:** BabyAI, Crafter, TextWorld, BabaIsAI, MiniHack, NetHack을 글자·이미지로 평가. arXiv 2411.13543.

**확인**
- 무효 출력에는 무효라는 피드백 후 기본 행동(예: 아무것도 안 함)을 실행하고 횟수를 기록.
- 이미지를 더하면 GPT-4o·Llama 3.2는 성적이 떨어지고 Claude 3.5 Sonnet·Gemini 1.5 Pro는 비슷.
- 아는 것과 하는 것의 차이(NetHack에서 즉사인 줄 알면서 던전 밖으로 나감).
- 전체 시연을 넣는 few-shot은 비용이 커 실행하지 못함.

**가져올 것:** (e) 무효·위법 출력 비율을 지표로. (a) 상태를 글자로 함께 주는 근거.

**가져오지 않을 것:** 무효 출력 때 기본 행동 실행. ARMY에서는 고르지 않은 입력이 됨. at-most-once·"제안만" 원칙과 충돌.

### Claude Plays Pokémon, Gemini Plays Pokémon, Continual Harness

**무엇:** 에뮬레이터 + 모델의 장기 플레이 하네스.

**확인**
- Claude 시작판(`davidhershey/ClaudePlaysPokemonStarter` 커밋 `2ab0551`, **LICENSE 파일 없음**): 도구 `press_buttons`(버튼 목록, 버튼마다 기다릴지), `navigate_to`(화면 9×10 격자의 칸으로 자동 이동, 경로는 코드 A*, `agent/emulator.py` `find_path`). 상태는 RAM에서 읽음(`get_state_from_memory`, 충돌 지도). 기록이 `max_history`(기본 60)를 넘으면 요약으로 교체(`agent/simple_agent.py` `summarize_history`).
- 공개 방송판의 지식 베이스 약 8,000토큰 상한, 행동마다 원본과 좌표 덧그림 두 장은 2차 자료(검색 요약)로만 봄. 미확인.
- Gemini Plays Pokémon(개발자 안내 2025-06-12 보관본): 화면에 격자 덧그림 + RAM 상태 글자, 약 100행동마다 요약, 길찾기·바위 퍼즐 전담 하위 에이전트(같은 모델에 알고리즘을 지시), 25턴마다 최근 100행동을 비평하는 관찰 에이전트. 탐색한 지도를 글자로 맥락에 넣음.
- 두 하네스 비교 글(LessWrong, Julian Bradshaw): 공통 요소 정리(조언 담은 시스템 프롬프트, 덧그림 스크린샷, RAM 정보, 계획 메모, 버튼 도구, 길찾기, 요약, 비평 인스턴스). 화면 판독 문제가 가장 큰 걸림돌이라는 관찰.
- Continual Harness(arXiv 2605.09998v1, 2026): GPP의 사람 개입 개선을 자동화해 에이전트가 플레이 중 자기 프롬프트·하위 에이전트·기술·기억을 고침. 최소 인터페이스에서 시작해 전문가 하네스와의 차이를 상당 부분 줄였다고 보고.

**가져올 것**
- (d) 모델은 "목적지 칸"만, 경로와 실제 입력은 코드(`navigate_to`). ARMY의 논리 행동 → `targets`와 같은 원칙의 근거.
- (a) 상태를 글자로(RAM 대신 ARMY 어댑터), 덧그림 스크린샷, 짧은 기록. 정적 게임은 판이 짧아 요약은 불필요(추론).

**가져오지 않을 것:** 에이전트가 자기 프롬프트·도구를 고치는 Continual Harness 방식. 프롬프트는 `blueprint/prompts/`에 고정, 도구는 Sentinel 통제(CLAUDE.md). 시작판 코드(라이선스 없음).

### GUI 에이전트: OSWorld, Set-of-Mark, OmniParser, UI-TARS, Agent S2

**무엇:** 일반 컴퓨터·웹 조작의 화면 근거 잡기(grounding). arXiv 2404.07972, 2310.11441, 2408.00203, 2501.12326, 2504.00906.

**확인**
- OSWorld: 행동 공간은 pyautogui 코드. 화면만으로 성공률 5.26~5.80%(당시), 접근성 트리를 쓴 LLM 2.37~12.24%, 사람 72.36%. 접근성 트리·SoM의 효과는 모델마다 다름(GPT-4o는 접근성 트리 11.21%, SoM 4.59%, 표 5).
- SoM: 분할 모델로 영역을 나누고 번호·상자를 덧그려 모델이 번호로 답하게 함.
- OmniParser: 상호작용 아이콘 검출·설명 모델을 따로 학습해 화면을 구조화.
- UI-TARS: 스크린샷만 보는 전용 모델, 플랫폼 공통 행동 공간(클릭·드래그·단축키). Agent S2: 일반 모델과 근거 잡기 전문 모델을 섞는 Mixture-of-Grounding.

**가져올 것:** (b) 번호 덧그림으로 답을 번호로 받는 것(요약 #3). DOM이 있으면 DOM 우선(OSWorld의 접근성 트리와 같은 이유).

**가져오지 않을 것:** 별도 검출·근거 잡기 모델 운영(무거움, 격자 게임에는 판 영역과 격자가 이미 정해짐). pyautogui 코드 행동 공간.

### 제외한 것

- **SIMA**(arXiv 2404.10179): 실시간 3D, 학습한 에이전트. 정적 격자 게임과 범위가 다름.
- **ViperGPT**(arXiv 2303.08128): 모델이 시각 모듈을 조합하는 Python을 쓰는 방식. 생성 판독 코드와 모양은 같지만 결과를 확인하는 장치가 없어 이 실험의 채택 시험에 줄 것이 없음(추론).
- **사이트별 판독 코드 생성 사례**: 찾지 못함. 코드 세계 모델 쪽은 모두 상태를 기호로 받음(위). 더 찾을 곳: 웹 스크래퍼 자동 생성, OCAtari 계열(미확인).

## 계획 반영 제안 (제안만, 실험 계획은 고치지 않음)

| 실험 계획 | 제안 | 근거 |
|---|---|---|
| §5 순서 2 "모델이 직접 k수를 둠" | 모델이 하는 일을 **읽기와 입력 위치 짚기**로 좁힘: 판 영역 이미지(번호 격자 덧그림) + DOM 글자로 상태 JSON과 행동 하나의 대상(칸 번호, 키)을 답함. **어느 수를 둘지는 규칙 모델의 `legal_actions`와 등록 풀이기(없으면 기본 정책)가 고름.** 드래그 좌표·집는 위치 보정은 계열 코드 | VideoGameBench 드래그 0~10%, BALROG, lmgame 하네스는 백엔드 상태, CWM for GGP의 LLM 직접 플레이 기권 |
| §5 순서 2 기록 | 기록 한 줄 = (관측 id, 모델이 읽은 상태, 행동, 다음 관측, 규칙 모델의 예측과 관측의 차이). 차이는 코드가 계산 | GamingAgent 기억 모듈, lmgame F.1 |
| §5 순서 4 ② | "연속 화면이 `assess`와 일치"를 **전이별 참·거짓 시험 목록**으로. 통과율, 실패 전이 목록을 저장(§4.4 채택 시험 결과). 무작위 게임은 관측 상태가 `outcomes` 집합 안인지 | CWM for GGP §4.1 |
| §5 순서 3·4 반복 | 실패 전이 하나와 차이를 되먹여 다시 생성. 실행 오류는 "고치기"(같은 코드, 오류만)로 먼저. 후보 여럿을 두고 통과율 높은 것과 덜 고친 것을 번갈아 고름. 재생성 상한은 이름 있는 상수(참고: Voyager 4회, GIF-MCTS 10~50회, CWM for GGP 숨은 정보 게임 중 잘 된 셋 4~23회) | GIF-MCTS, CWM for GGP, Voyager |
| §5 순서 4 확인용 기록 | 기록 중 일부(예: 마지막 전이들)는 생성에 보이지 않고 채택에만 씀 | CWM for GGP 표 1의 학습·확인 분리 |
| §5 순서 4 ②실패 + ④일치 → 규칙 변형 | 한 화면 VLM 확인 하나로 "규칙이 다름"을 정하지 않음. 후보(어댑터 재생성, 규칙 값 변경, 규칙 코드 덧붙임)를 같은 기록에 돌려 **모든 전이를 설명하는 가장 작은 변경**을 채택. 규칙 값 → 코드 덧붙임 순 | WorldCoder, PoE-World, WALL-E |
| §5 순서 6 매 턴 VLM 바닥 | 한 번 호출로: 규칙 요약(계열 설명) + 부분 상태 JSON(`null` 포함) + 판 영역 덧그림 이미지 + 합법 행동 목록 + 최근 N수 기록 → 행동 id 하나. 목록 밖이면 한 번 다시 묻고, 또 틀리면 멈춤(기본 행동 없음). 반성·요약 호출 없음 | GamingAgent(호출 수 줄임), BALROG(기본 행동은 버림) |
| §5 순서 6 플레이 | 수마다의 효과 확인 결과를 "온라인 전이 정확도"로 기록. 어댑터 재생성 기준(반복 불일치)도 이 값으로 | CWM for GGP 표 1 온라인 열 |
| §5 끝 장면 | `won`·`over`는 처음 k수에 거의 안 나옴. 채택 시험 통과를 끝 판정이 맞다는 근거로 쓰지 않음. 어댑터가 처음 보는 장면은 지금처럼 VLM 장면 판정으로 | GIF-MCTS 정확도-보상 차이, 8a의 승리 화면 실패 |
| §8 단계 6 | 생성 규칙 변형에 replay 무작위 플레이 시험(예외 없음, 판이 끝남, `legal_actions`가 비지 않음)을 더함 | CWM for GGP §4.2, WorldCoder 낙관 조건 |
| §3 휴리스틱 `evaluate` 생성 | 정답이 없으니 여럿 생성 → replay 토너먼트로 하나 고름 | CWM for GGP §4.3 |
| §9 지표 | 추가: 정규화 점수 (생성 − 무작위)/(기준 − 무작위), 전이 정확도 학습·확인·온라인, 채택까지 모델 호출 수, 매 턴 VLM의 무효·위법 답 비율. 2048은 10턴 무변화 끝 규칙과 10×log2(합친 값 합) 점수를 함께 기록해 lmgame 수치와 비교 가능하게 | GIF-MCTS, lmgame-Bench, BALROG |
| §6 안전 | 바꿀 것 없음. 조사한 하네스 중 모델 코드를 입력 경로에서 실행하는 것(GamingAgent `exec`, Cradle 기술 코드, OSWorld pyautogui 코드)은 모두 가져오지 않음을 명시 | 위 각 절 |

## 출처

저장소 (2026-11-13 받음)
- GamingAgent: https://github.com/lmgame-org/GamingAgent 커밋 `996d848ae5e3bf68433d663f38ef4da5bdfe5332`. 본 파일: `gamingagent/agents/base_agent.py`, `gamingagent/modules/{perception,memory,reasoning}_module.py`, `gamingagent/configs/custom_01_2048/{config.yaml,module_prompts.json}`, `gamingagent/envs/gym_env_adapter.py`, `gamingagent/envs/custom_01_2048/twentyFortyEightEnv.py`, `tools/utils.py`, `computer_use/games/{game_2048,tetris,superMario}/workers.py`, `requirements.txt`, `LICENSE`
- ClaudePlaysPokemonStarter: https://github.com/davidhershey/ClaudePlaysPokemonStarter 커밋 `2ab0551016aba9af3ffa6fbb6e747d1ec33c08f8`. 본 파일: `agent/simple_agent.py`, `agent/emulator.py`

논문 (arXiv 본문 확인)
- lmgame-Bench: https://arxiv.org/abs/2505.15146 (§2.2.1, 부록 E.2, F.1, G)
- Code World Models for General Game Playing: https://arxiv.org/abs/2510.04542 (§4.1~4.3, §5.1, 표 1, 부록 표 7)
- GIF-MCTS: https://arxiv.org/abs/2405.15383 (표 2, 표 3, 정확도-보상 차이)
- WorldCoder: https://arxiv.org/abs/2402.12275 (§2.2, §2.5, §3, §5)
- PoE-World: https://arxiv.org/abs/2505.10819
- WALL-E: https://arxiv.org/abs/2410.07484
- Voyager: https://arxiv.org/abs/2305.16291
- Cradle: https://arxiv.org/abs/2403.03186
- VideoGameBench: https://arxiv.org/abs/2505.18134 (표 4, 진행 측정)
- BALROG: https://arxiv.org/abs/2411.13543
- OSWorld: https://arxiv.org/abs/2404.07972 (표 5)
- Set-of-Mark: https://arxiv.org/abs/2310.11441
- OmniParser: https://arxiv.org/abs/2408.00203
- UI-TARS: https://arxiv.org/abs/2501.12326
- Agent S2: https://arxiv.org/abs/2504.00906
- SIMA: https://arxiv.org/abs/2404.10179
- ViperGPT: https://arxiv.org/abs/2303.08128
- Continual Harness: https://arxiv.org/abs/2605.09998

글
- Gemini Plays Pokémon 안내(2025-06-12 보관본): https://rentry.org/gpp-about-20250612
- Is Gemini now better than Claude at Pokémon? (LessWrong): https://www.lesswrong.com/posts/7mqp8uRnnPdbBzJZE/is-gemini-now-better-than-claude-at-pokemon

미확인으로 남긴 것
- CWM for GGP, WorldCoder, GIF-MCTS 코드의 라이선스와 공개 여부(코드는 반입하지 않으므로 아이디어 차용에는 영향 없음).
- Claude Plays Pokémon 방송판 하네스의 세부(지식 베이스 상한, 스크린샷 두 장). 공식 원문 대신 2차 요약만 봄.
- 사이트별 판독 코드를 생성해 검증하는 공개 연구의 존재.
