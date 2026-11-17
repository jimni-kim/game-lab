# 3매치 게임의 규칙과 특수 조각 (2026-11-17)

`game-lab` 에뮬레이터에 깔린 3매치 게임과, 각 게임의 규칙·특수 조각을 조사한 기록이다. 규칙은 공개 안내(제작사 도움말, 위키, 공략)에서 가져왔고, 게임 안에서 직접 확인한 것은 따로 적었다. 목적은 3매치 키트(PLAN §20 X4)와 규칙 모델(`rules/match3.py`)이 무엇을 모르는지 보는 것이다.

## 설치된 게임

| 게임 | 패키지 | 방식 | 설치 |
|---|---|---|---|
| 캔디크러쉬사가 | `com.king.candycrushsaga` | 바꾸기(swap) | 전부터. 나이 확인은 사용자가 넘김(2026-11-17) |
| 로얄 매치 | `com.dreamgames.royalmatch` | 바꾸기 | 전부터 |
| 캔디크러쉬 소다 | `com.king.candycrushsodasaga` | 바꾸기 | 2026-11-17 Play 스토어 |
| 꿈의 집(Homescapes) | `com.playrix.homescapes` | 바꾸기 | 2026-11-17 Play 스토어 |
| 툰 블라스트 | `net.peakgames.toonblast` | 누르기(blast) | **설치 실패**(2026-11-17, Play 스토어: 저장 공간 부족) |

같은 기기에 있던 Match Factory와 Tile Club은 이름과 달리 타일을 골라 모으는 게임이라 이 계열이 아니다(아래 정리 때 지움).

**앱 정리(2026-11-17, 사용자 지시).** 구현한 계열(블록 놓기, 2048, 지뢰찾기, 3매치)의 게임과 Puzzles만 남기고 14개를 지웠다: Block Blast Lite(이 에뮬레이터에서 안 뜸), Brain Puzzle 3, 체스닷컴, Sudoku.com, 틀린그림찾기, Tile Club, Bolt Out, 가십 하버, Meowdoku, SoEasy 2248, 디즈니 솔리테어, Bus Fever, Match Factory, 낱말퀴즈. 남은 것: Block Game, Woodoku, 1010!, Block Bloom, Block Crush!, Color Block, 2048, Puzzles, 캔디크러쉬사가, 캔디크러쉬 소다, 로얄 매치, 꿈의 집. 데이터 영역은 10GB 중 4.4GB가 비었다.

툰 블라스트는 정리 전에 공간이 모자라 못 깔았고, 누르기형이라 구현한 계열이 아니므로 다시 받지 않았다.

## 공통 규칙 (바꾸기형)

- 이웃한 두 조각을 바꿔 같은 종류 셋 이상이 한 줄이 되면 지워진다. 줄이 안 생기는 바꾸기는 되돌아간다.
- 지워진 자리는 위의 조각이 떨어져 메우고, 맨 위에서 새 조각이 들어온다. 그 결과 다시 줄이 생기면 연쇄로 지워진다.
- **넷 이상을 한 번에 맞추면 그 자리에 특수 조각이 생긴다.** 모양(일자, ㄱ·T자, 2×2)에 따라 종류가 갈린다.
- 특수 조각은 줄에 끼워 지우거나, 게임에 따라 그냥 눌러서, 또는 이웃 조각과 바꿔서 터뜨린다. **특수 조각 둘을 서로 바꾸면 줄이 없어도 받아들여지고** 더 큰 효과가 난다.
- 판은 네모가 아닐 수 있고(빈 자리, 벽), 조각이 아닌 것(장애물, 덮개, 사슬)이 칸을 차지한다. 장애물은 대개 옆에서 줄을 맞추거나 특수 조각으로 깬다.
- 한 판(레벨)은 수 제한 안에 목표(특정 조각 N개, 장애물 전부, 젤리 전부 등)를 채우는 것이고, 끝나면 다음 레벨 화면으로 넘어간다.

## 게임별 특수 조각

### 캔디크러쉬사가

| 만드는 법 | 조각 | 효과 |
|---|---|---|
| 일자 4개 | 줄무늬 사탕 | 줄무늬 방향의 한 줄(행 또는 열) |
| ㄱ·T자 5개 | 포장 사탕 | 둘레 3×3을 두 번 |
| 일자 5개 | 컬러 봄 | 바꾼 상대 색을 판에서 전부 |

조합: 줄무늬+줄무늬는 행과 열, 컬러 봄+컬러 봄은 판 전체(검색한 자료). 줄무늬+포장은 세 행과 세 열, 컬러 봄+줄무늬는 그 색을 전부 줄무늬로 바꿔 터뜨림(널리 알려진 규칙이나 이번 검색 결과로는 확인하지 못함. 포장 사탕과 컬러 봄을 만드는 모양도 같음).

직접 본 것(2026-11-17, 레벨 1): 8×8, 사탕 네 종류(파랑·주황·초록·보라), 칸 사이에 선이 없음, 줄무늬 사탕 하나가 판에 있었음. 5수째에 레벨이 끝나 "Level 2 시작" 화면이 떴다.

**2×2도 특수 조각이 된다**(사용자, 2026-11-17: 사가에서도 2×2를 맞추면 물고기가 생기고 효과도 같다. 검색한 옛 공략은 "사가에는 없다"고 했으나 지금 게임과 다르다). 그러면 네 게임 모두 네 가지 모양(일자 4, 2×2, ㄱ·T 5, 일자 5)이 같다.

### 캔디크러쉬 소다

사가의 줄무늬·포장·컬러 봄에 더해:

| 만드는 법 | 조각 | 효과 |
|---|---|---|
| 2×2 네 개 | 물고기(Swedish Fish) | 목표물 하나를 찾아가 지움 |
| 더 큰 묶음 | 컬러링 사탕 | 한 색을 다른 색으로 바꿈. 만드는 정확한 조건은 확인하지 못함 |

소다가 사가와 다른 점으로 알려진 것은 컬러링 사탕과 소다 수위다. 소다 레벨은 병을 터뜨려 소다 수위를 올리면 그 아래에서는 조각이 위로 떠오른다(중력이 뒤집힘)고 알려져 있으나 직접 보지 않았다.

### 로얄 매치

| 만드는 법 | 조각 | 효과 |
|---|---|---|
| 일자 4개 | 로켓 | 향한 방향의 한 행 또는 한 열 |
| 2×2 네 개 | 프로펠러 | 임의의 목표 하나(둘레도 조금) |
| ㄱ·T자 5개 | TNT | 반경 2칸 |
| 일자 5개 | 라이트 볼 | 한 색 전부 |

조합: 로켓+로켓은 행과 열, 로켓+TNT는 세 행과 세 열, TNT+TNT는 반경 4칸, 프로펠러+로켓/TNT는 그것을 목표가 많은 곳으로 실어 나름, 라이트 볼+다른 것은 가장 많은 색을 전부 그 조각으로 바꿈, 라이트 볼+라이트 볼은 판 전체. 프로펠러+프로펠러는 자료마다 설명이 다르다. 특수 조각은 눌러도 터진다.

### 꿈의 집 (Homescapes)

| 만드는 법 | 조각 | 효과 |
|---|---|---|
| 일자 4개 | 로켓 | 한 행 또는 한 열 |
| 2×2 네 개 | 종이비행기 | 둘레 네 칸, 그리고 다른 곳 한 칸 |
| ㄱ·T자 5개 | 폭탄 | 둘레 넓게 |
| 일자 5개 | 무지개 공 | 한 종류 전부 |

특수 조각끼리 바꾸면 조합 효과(예: 로켓+무지개 공은 판 곳곳에 로켓). 장애물마다 다르게 막는다: 상자는 로켓이 지나가고 쿠키는 막는다. 특수 조각은 두 번 누르면 터진다.

### 툰 블라스트 (누르기형, 다른 계열)

바꾸지 않는다. **같은 색이 둘 이상 붙어 있는 덩어리를 누르면** 지워진다. 덩어리 크기로 특수 조각이 생긴다.

| 덩어리 크기 | 조각 | 효과 |
|---|---|---|
| 5개 | 로켓 | 한 행 또는 한 열(방향은 임의라는 자료가 있음) |
| 7개(6개라는 자료도 있음) | 폭탄 | 둘레 3×3 |
| 9개 이상 | 디스코 볼 | 한 색 전부 |

조합은 로얄 매치와 같은 꼴이다. 이 게임은 `match3` 계열이 아니라 Simon Tatham의 Same Game과 같은 "덩어리 지우기"다(game-lab에서는 Same Game을 만든 계열로 둔 적이 있다. 보관분 `out/review/adapters-h4c-samegame`).

## 키트와 규칙 모델이 지금 모르는 것

지금 3매치 키트는 조각을 모습으로 묶어 종류 번호로 읽고, 배운 모습이 아닌 칸은 "못 쓰는 칸"으로 읽는다. 규칙 모델은 일자 3개 이상만 줄로 본다.

| 게임이 하는 것 | 지금 | 필요한 것 |
|---|---|---|
| 일자 3개 맞추기, 떨어지기, 연쇄 | 됨 | — |
| 네모가 아닌 판, 장애물 칸 | 못 쓰는 칸으로 읽음 | 장애물이 "옆에서 맞추면 깨진다"는 것은 모름. 목표가 장애물이면 일부러 그쪽을 두지 않음 |
| 특수 조각이 판에 생김 | 못 쓰는 칸으로 읽음(벽 취급) | 종류를 구분해 읽기(게임마다 그림이 다름), 그리고 "움직일 수 있는 칸"으로 다루기. 지금은 특수 조각 아래의 조각이 떨어지지 않는다고 잘못 예측함 |
| 특수 조각을 줄에 끼워 터뜨림 | 안 둠 | 특수 조각의 원래 색을 알아야 함(줄무늬 사탕은 색이 남고, 로켓·TNT는 색이 없음) |
| 특수 조각끼리, 또는 특수 조각과 아무 조각을 바꿈 | 안 둠(규칙이 불법으로 봄) | 유효한 수로 넣기 |
| 특수 조각을 눌러 터뜨림(로얄 매치, 꿈의 집) | 입력 종류가 없음(끌기만) | 탭 입력 |
| 2×2 맞추기(네 게임 모두) | 유효한 수로 안 봄 | 규칙 매개변수(`square`) |
| 넷·다섯을 노려 특수 조각 만들기 | 풀이기가 지운 수만 셈 | 평가에 반영 |
| 레벨 목표, 남은 수 | 안 읽음 | 목표를 읽어 수를 고르기. 지금은 "수를 많이 남기는 판"만 봄 |
| 레벨이 끝난 뒤 다음 레벨 | 게임의 끝으로 판정 | 다음 레벨로 넘어가 이어 두기(블록 게임과 같은 문제) |
| 소다의 뒤집힌 중력 | 모름 | 떨어지는 방향을 규칙 값으로 |

효과의 정확한 범위(TNT 반경 등)는 규칙이 몰라도 된다. 3매치는 이미 "수 뒤의 판은 멈출 때까지 다시 읽는다"로 두므로, 필요한 것은 **무엇이 유효한 수인가**와 **어느 수가 좋은가**다.

순서를 매기면: (1) 특수 조각을 "움직이는 칸"으로 읽고 특수 조각이 낀 바꾸기를 유효한 수로 넣기 → (2) 2×2 → (3) 탭으로 터뜨리기 → (4) 레벨 목표 읽기. (1)만으로도 판에 특수 조각이 쌓여 둘 곳이 줄어드는 문제가 풀린다.

## 확인하지 못한 것

- 소다·꿈의 집·로얄 매치·툰 블라스트는 게임 안에서 직접 두어 보지 않았다. 위 표는 공개 자료 기준이다.
- 소다의 컬러링 사탕을 만드는 조건, 툰 블라스트의 폭탄 개수(6 또는 7), 로얄 매치의 프로펠러+프로펠러는 자료가 갈리거나 없었다.
- 각 게임이 처음 켤 때 튜토리얼·로그인 권유·나이 확인을 띄우는지는 보지 않았다.

## 출처

- [Royal Match: Creating and using the power-ups (Dream Games)](https://dreamgames.helpshift.com/hc/az/3-royal-match/faq/6-creating-and-using-the-power-ups)
- [Royal Match: Power-up combinations (Dream Games)](https://dreamgames.helpshift.com/hc/az/3-royal-match/faq/7-power-up-combinations)
- [Royal Match Wiki: Rocket Combinations](https://royalmatch.fandom.com/wiki/Rocket_Combinations)
- [Homescapes: Power-ups and how to use them (Playrix)](https://playrix.helpshift.com/hc/en/14-homescapes/faq/1088-power-ups-and-how-to-use-them)
- [Homescapes power-ups guide (SuperCheats)](https://www.supercheats.com/homescapes-walkthrough-guide/power-ups-guide)
- [Candy Crush Saga: Special Candy Combinations (Casual Game Guides)](https://casualgameguides.com/walkthroughs/candy-crush-saga/special-candy-combinations)
- [Candy Crush Saga Candy Combinations (GameRevolution)](https://www.gamerevolution.com/guides/60253-candy-crush-saga-candy-combinations)
- [Candy Crush Soda Wiki: Candies](https://candycrushsoda.fandom.com/wiki/Candies)
- [Candy Crush Soda Wiki: Coloring Candy](https://candycrushsoda.fandom.com/wiki/Coloring_Candy)
- [Candy Crush Soda Saga: Special Candy and Combos Guide (GameSkinny)](https://www.gameskinny.com/1c0lw/candy-crush-soda-saga-special-candy-and-combos-guide)
- [Toon Blast cheats, tips and tricks (Pocket Gamer)](https://www.pocketgamer.com/toon-blast/toon-blast-cheats-tips-and-tricks/)
- [Toon Blast tips, cheats and strategies (Gamezebo)](https://www.gamezebo.com/walkthroughs/toon-blast-tips-cheats-and-strategies/)
