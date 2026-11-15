# Google Play 상위 200위권의 정적 게임 목록 (2026-11-15)

## 확인한 차트와 범위

- 출처는 AppBrain 순위 페이지 6개이고, 모두 페이지에 "Last updated: November 15, 2026"이라고 적혀 있습니다. 2026-11-15에 받았습니다.
  - 한국 인기 무료 게임 1–200위: `appbrain.com/stats/google-play-rankings/top_free/game/kr`, `/kr/2`
  - 한국 최고 매출 게임 1–200위: `.../top_grossing/game/kr`, `/kr/2`
  - 미국 인기 무료 게임 1–200위: `.../top_free/game/us`, `/us/2`. 미국 24위는 원문에 빠져 있어 199개만 확인했습니다.
- 세 차트 모두 **200위까지 확인했습니다**. 순위는 그날 하루 치 스냅숏입니다. 날마다 바뀌고, 특히 무료 차트 하위권은 많이 움직입니다.
- 주의할 점이 있습니다. 순위표는 WebFetch의 요약 모델이 페이지를 표로 옮긴 결과입니다. 원문 HTML과 한 줄씩 대조하지는 않았으므로 몇 위 정도는 어긋날 수 있습니다. 패키지 ID도 같은 출처에서 왔습니다.
- 표기 방법: `KR무료 12` = 한국 인기 무료 12위, `KR매출 8` = 한국 최고 매출 8위, `US무료 5` = 미국 인기 무료 5위. 날짜는 모두 2026-11-15입니다.
- Google Play 앱 페이지(다운로드 수)와 AppBrain 앱 상세 페이지는 이번 환경에서 404·403이 나거나 본문이 잘려 읽지 못했습니다. **그래서 다운로드 수는 하나도 확인하지 못했습니다.**

### 표를 읽는 법

- "정적 판정"과 "걸림돌"은 차트에서 나온 사실이 아니라 **일반 지식에 근거한 판단**입니다. 이번 조사에서 게임을 직접 실행하거나 각 게임의 문서를 확인하지 않았습니다. 확신이 낮은 항목에는 (?)를 붙였습니다.
- 정적 판정 기준
  - **완전 정적**: 입력 전까지 무한정 기다립니다.
  - **대체로 정적**: 레벨 시간 제한이나 턴 타이머가 있거나, 일부 모드만 실시간입니다.
  - **애매**: 이유를 함께 적었습니다.
- 걸림돌에 쓴 줄임말
  - 생명: 실패하면 줄어드는 하트·생명
  - 에너지: 행동할 때 쓰는 에너지
  - 광고: 강제 전면 광고
  - 온라인: 온라인 연결 필수
  - 계정: 로그인이나 계정 연동 필요
  - PvP: 다른 사람과 하는 대전

---

## 1. 매치3·블라스트

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| 로얄 매치 (Royal Match) | com.dreamgames.royalmatch | KR무료 80, KR매출 14, US무료 155 | 완전 정적 (수 제한) | 생명, 진행할수록 어려워짐 |
| 로얄 킹덤 (Royal Kingdom) | com.dreamgames.royalkingdom | KR무료 23, KR매출 27, US무료 55 | 완전 정적 | 생명 |
| 캔디크러쉬사가 (Candy Crush Saga) | com.king.candycrushsaga | KR매출 57, KR무료 166, US무료 44 | 완전 정적. 극히 일부 레벨에만 시간 제한이 있다고 알려져 있음(?) | 생명, King 계정 연동 권유 |
| 꿈의 정원 (Gardenscapes) | com.playrix.gardenscapes | KR매출 31, US무료 158 | 완전 정적 (매치3 부분) | 생명, 꾸미기 연출과 튜토리얼이 긺 |
| 꿈의 집 (Homescapes) | com.playrix.homescapes | KR매출 59 | 완전 정적 | 생명 |
| 피시덤 (Fishdom) | com.playrix.fishdomdd.gplay | KR매출 86 | 완전 정적 | 생명, 미니게임 광고 |
| 툰 블라스트 (Toon Blast) | net.peakgames.toonblast | KR매출 199, US무료 159 | 완전 정적 | 생명 |
| 애니팡2 | com.sundaytoz.mobile.anipang2.google.kakao.service | KR매출 147 | 대체로 정적 (모드에 따라 시간제 스테이지(?)) | 카카오 로그인, 하트 |
| 애니팡4 | com.sundaytoz.kakao.anipang4 | KR매출 174 | 완전 정적 (수 제한 위주) | 카카오 로그인, 하트 |
| 애니팡3 | com.sundaytoz.kakao.anipang3.service | KR매출 190 | 완전 정적 (?) | 카카오 로그인, 하트 |
| 프렌즈팝콘 (Friends Popcorn) | com.kakaogames.friendsKing | KR매출 167 | 완전 정적 (육각 매치3) | 카카오 로그인, 하트 |
| 크라운 크러쉬 (Crown Crush) | com.gl.crowncrush | US무료 61 | 완전 정적 (?) | 생명 (?) |
| 슈가 러시 어드벤처 (Sugar Rush Adventure) | com.gimica.sugarmatch | US무료 105 | 완전 정적 (?) | 알 수 없음 |
| 매칭 스토리 (Matching Story) | com.joycastle.mergematch | KR매출 162 | 완전 정적 (?) | 알 수 없음 |
| 엠파이어 앤 퍼즐 (Empires & Puzzles) | com.smallgiantgames.empires | KR매출 188 | 대체로 정적. 매치3 전투는 기다려 주고, 연맹 이벤트는 기한이 있음 | 온라인, PvP 방어전 |

## 2. 타일 매치·마작·3D 트리플 매치

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| Vita Mahjong | com.vitastudio.mahjong | KR무료 75, US무료 14 | 완전 정적 | 광고 |
| Mahjong Blast | com.nebula.mahjongtile | KR무료 66, US무료 17 | 완전 정적 (?) | 광고 |
| Tap Tile! | com.nebula.tiles | KR무료 100, US무료 146 | 완전 정적 (?) | 광고 |
| Tile Club | com.gamovation.tileclub | KR무료 162 | 완전 정적 | 광고 |
| 타일 익스플로러 (Tile Explorer) | com.oakever.tiletrip | US무료 118 | 완전 정적 | 광고 |
| Slide Mahjong: Beauty Blast | com.SlideMahjong.Beauty.Blast.LuckyGirl.Tile.Love | KR무료 137 | 완전 정적 (?) | 광고 |
| Mahjong Master / Vigor Mahjong / Mahjong Smart | com.mahjong.master.solitaire.match.puzzle / com.godlike.vigormahjong / com.gl.mahjong | US무료 75 / 96 / 133 | 완전 정적 | 광고 |
| Goods Triple Match: Sorting 3D | com.goods.sorting.games.triple.match3d.puzzle | KR무료 65 | 대체로 정적 (3D 물건 고르기, 레벨 타이머 흔함) | 3D 물체 판별, 광고 |
| Goods Puzzle: 3D Sorting / Goods Rush! 3D Sort | sorting.games.goods.sort.triple.match3d.puzzle.stuff / com.triple.match.goods.sort.puzzle.game | US무료 83 / 134 | 대체로 정적 (타이머 (?)) | 광고 |
| 매치 팩토리 (Match Factory!) | net.peakgames.match | US무료 174 | 대체로 정적 (레벨 타이머) | 3D 물체 더미, 생명 |

## 3. 블록 놓기·블록 퍼즐

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| 블록 블라스트 (Block Blast!) | com.block.juggle | KR무료 12, US무료 2 | 완전 정적 | 광고 |
| Block Blast Lite | com.blockblast.lite | KR무료 24, US무료 39 | 완전 정적 | 광고 |
| Block Game (mocacorp) | com.mocacorp.block | KR무료 10 | 완전 정적 (?) | 광고 |
| Block Crush! (우드 블록류) | com.wood.block.sudoku.puzzle.bm | KR무료 69, US무료 188 | 완전 정적 | 광고 |
| 우도쿠 블라스트 (Woodoku Blast) | com.tripledot.blockbash | US무료 69 | 완전 정적 | 광고 |
| Color Block: Combo Blast | com.puzzlegames.puzzlebrickslegend | KR무료 95, US무료 98 | 완전 정적 (?) | 광고 |
| Block Bloom | com.nebula.blockbloom | KR무료 154 | 완전 정적 (?) | 광고 |
| Sand Blocks: Drop Puzzle | com.uncosoft.wordpuzzle | KR무료 141, US무료 94 | 대체로 정적. 놓은 뒤 모래가 흘러내리는 물리를 기다려야 함 (?) | 광고 |
| Stack Smash / Maze Escape: Block Puzzle | com.tripledot.blockssmashgame / com.fiogonia.maze | US무료 165 / 194 | 애매 (규칙 미확인) | 알 수 없음 |

## 4. 정렬(sort)·잼·나사·화살표 퍼즐

요즘 하이퍼캐주얼 퍼즐에서 가장 큰 무리입니다. 판은 대부분 기다려 주지만, 레벨 타이머가 붙은 게임이 많습니다.

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| Magic Sort! (워터 소트) | com.grandgames.magicsort | KR무료 18, US무료 7 | 완전 정적 | 광고 |
| Pink Pour: Water Sort | com.yourstudio.pinkpour | KR무료 52, US무료 131 | 완전 정적 | 광고 |
| Water Sort! Color Sort Puzzle | water.sort.color.puzzle.offline.games | KR무료 145, US무료 163 | 완전 정적 | 광고 |
| Ball Sort Puzzle / Fish Sort Puzzle | ball.sort.puzzle.color.sorting.bubble.games / triple.sorting.bubble.fish.match | US무료 106 / 150 | 완전 정적 | 광고 |
| Cutie Sort / Sort Streak / Pocket Sort | sort.casual.color.cat.puzzle.match / com.oakever.sortparade / com.pocket.sort.coin.puzzle.game | KR무료 25·40·78, US무료 48·26·28 | 완전 정적 (?) | 광고 |
| Mochiroom Puzzle: Room Sort | com.gamefactory.roomsort | KR무료 101, US무료 31 | 완전 정적 (?) | 광고 |
| Skewer Jam / Rope Sort! / Hexa Smash | com.ig.skewer.jam.food.sort / com.nebula.ropesort / com.hexa.smash.sort.puzzle.game.HexaSmash | KR무료 59 / 187 / 182 | 대체로 정적 (?) | 광고 |
| Bus Fever Party! (버스 잼) | gridplus.busjam.carpuzzle | KR무료 33, US무료 11 | 대체로 정적 (버스 잼류는 레벨 타이머가 흔함) | 광고 |
| Bus Crowd / Bus Traffic Fever! / Block Bus! / Jam Bus: Sandfall | busflow.traffic.puzzle / jp.co.goodroid.hyper.busflow / com.gl.bus / bus.sand.fall.jam.loop.traffic.flow | KR무료 85·155, US무료 57·115·103·58 | 대체로 정적 (타이머 (?)) | 광고 |
| Car Jam Solver / Color Puzzle Jam / Parking Loop / CarLoop | car.parking.jam.solver.puzzle.games / bus.match.traffic.car.puzzles.parking / com.loop.packedloop / com.blast.puzzlegame.bear | KR무료 112·178·79, US무료 27·68 | 대체로 정적 (?) | 광고 |
| Block Out! / Color Block Jam (블록 밀어내기) | com.grandgames.catblockjam / com.GybeGames.ColorBlockJam | KR무료 134, US무료 56·86 | 대체로 정적 (Color Block Jam에는 레벨 타이머가 있다고 알고 있음) | 끌기 입력, 광고 |
| Wool Crush / Wool Connect / Yarn Loop | wool.match.color.sort.jam.puzzle / com.arcloom.wool.connect.yarn.puzzle.game / com.combo.yarnflow | KR무료 113·67·118, US무료 73·21 | 대체로 정적 (?) | 광고 |
| Piggy Escape / Colony Flow! / Pixel Flow! / Pixelora Flow | piggy.escape.jam.farm.puzzle.game / com.abi.colony.flow / com.loomgames.pixelflow / pixelora.flow.pixel.art.snake.puzzle.game | KR무료 153·88·116, KR매출 150(Colony Flow), US무료 78·66·199 | 애매 (?) (규칙 미확인. 흐름 잇기나 잼 계열로 추정) | 알 수 없음 |
| 볼트 아웃 (Bolt Out, 나사 퍼즐) | com.gl.unbolt | KR무료 180, US무료 9 | 대체로 정적 (나사류는 레벨 타이머가 흔함) | 광고 |
| Screw Out 3D / Screwdom 3D / Unscrew Frenzy 3D | net.happywit.screw.out.jam / com.ig.screwdom / com.kiwifun.game.android.screw.master | KR무료 170·171, US무료 52·185 | 대체로 정적 (타이머 (?)) | 3D 회전, 광고 |
| Arrows – Puzzle Escape / Arrow Puzzle (Easybrain) / Arrow Puzzle 3D / Arrow Puzzle: Beauty Prize | com.ecffri.arrows / com.easybrain.arrow.puzzle.game / com.easybrain.arrow.puzzle3d / com.arrpuzbeapri.apbp | KR무료 97·161·160, US무료 59·49·172 | 완전 정적 (화살표 빼내기) | 광고 |
| Amaze GO! / GOGO! Blast / MOVLO / CubeAway | com.oakever.arrows / com.nebula.arrows / com.arrowflow.escape.puzzle.game / jp.co.goodroid.hyper.puzzle.arrows3dcubepuzzle | KR무료 43·57·41·92, US무료 25·121·189·72 | 완전 정적 (?) | 3D 회전(CubeAway), 광고 |
| Rotate Rings / Loop Sort / Twist Colors / Cube Land | com.nebula.rotaterings / com.Garawell.LoopSort / cube.clear.burst.spin.sweep.puzzle.rotate / com.rotatelab.cubeblast | KR무료 17·111·165, US무료 6·53·18 | 완전 정적 (?) | 광고 |
| Hole Stars | com.moonactive.holestars | US무료 47 | 애매 (구멍을 움직이는 방식이면 실시간일 수 있음) | 알 수 없음 |

## 5. 숫자·논리 (스도쿠·2048류)

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| Meowdoku | com.oakever.meowdoku | KR무료 19, US무료 1 | 완전 정적 | 광고 |
| Sudoku (mocacorp) | com.mocacorp.sudoku | KR무료 13 | 완전 정적 | 광고 |
| Hamdoku | com.mobirix.hamdoku | KR무료 27 | 완전 정적 | 광고 |
| 스도쿠닷컴 (Sudoku.com) | com.easybrain.sudoku.android | KR무료 90, US무료 180 | 완전 정적 (시간은 기록만 함) | 광고 |
| Sudoku - Classic / Colordoku / Zoodoku | easy.sudoku.puzzle.solver.free / com.clarkstudio.an.queen.colordoku / com.nebula.zoodoku | KR무료 115·168, US무료 200 | 완전 정적 | 광고 |
| SoEasy 2248 (2048류) | com.sofish.sz2248.gp | KR무료 186 | 완전 정적 | 광고 |
| Daily Number Match | daily.number.match.free.puzzle | US무료 192 | 완전 정적 | 광고 |
| Watermelon Maker 2048 (수박게임류) | com.mobirix.ldwm | KR무료 60 | 대체로 정적. 떨어뜨린 뒤 물리가 가라앉기를 기다려야 함 (?) | 광고 |

## 6. 두뇌·퀴즈·단어

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| Brain Puzzle 3 / Brain Puzzle 2 | com.brain.crazy.joygame / com.brain.logic.joygame | KR무료 49·107, US무료 51·190 | 완전 정적 (?) | 문제마다 조작이 다름, 광고 |
| 낱말퀴즈 | word.games.puzzle.offline.games | KR무료 127 | 완전 정적 | 광고 |
| Guess Their Answer | com.qoni.guesstheiranswer | KR무료 132 | 완전 정적 (?) | 영어 |
| 워드 스케이프 (Wordscapes) / Wordscapes Search | com.peoplefun.wordcross / com.peoplefun.wordsearch | US무료 123·151 | 완전 정적 | 영어, 광고 |
| Word Tiles / Word Search Explorer / Crossword Go! / Word Connect Association | com.agavegames.wordtiles / in.playsimple.wordsearch / in.playsimple.crossword.go / in.playsimple.wordtrip.association | US무료 16·32·60·62 | 완전 정적 | 영어, 광고 |
| Solitaire Associations Journey | com.hitappsgames.wordsolitaire | KR무료 194, US무료 114 | 완전 정적 | 영어 |
| Word Music - Crossword | com.wordgame.wordcross.puzzle | KR무료 198 | 완전 정적 | 광고 |

## 7. 숨은그림·틀린그림 찾기·컬러링

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| Search It - Hidden Objects | com.leap.searchit | KR무료 50, US무료 71 | 완전 정적 (?) | 큰 그림 확대·이동 |
| Find Hidden Objects - Spot It! | com.yolo.hiddenobjects | KR무료 183, US무료 90 | 완전 정적 (?) | 확대·이동 |
| Hidden Object Games: Seek It / Find The Cat 2 | hidden.object.find.seek.search.mystery.puzzle.games.free / com.agavegames.findthecat2 | US무료 87·116 | 완전 정적 (?) | 확대·이동 |
| 틀린그림찾기 (Difference: Spot & Find / Differences - Find & Spot It) | com.find.spot.photo.hunt.differences.free.hidden / find.difference.spot.differences.hidden.puzzle.games.free | KR무료 48·175 | 대체로 정적 (틀린그림류는 타이머가 흔함) | 오답 감점, 광고 |
| Food Hunt: Pixel Puzzle | com.vigafun.funfinity.foodhunt | KR무료 44, US무료 108 | 애매 (?) (규칙 미확인) | 알 수 없음 |
| Jewel Coloring / Happy Color / Color by Number | color.number.paint.pixle.art.sort.jigsaw / com.pixel.art.coloring.color.number / com.tfgco.apps.coloring.free.color.by.number | KR무료 87, US무료 74·93 | 완전 정적 (승패가 없는 색칠) | 칸이 아주 작음 |

## 8. 솔리테어·카드·주사위·보드

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| 디즈니 솔리테어 (Disney Solitaire) | com.superplaystudios.disneysolitairedreams | KR무료 68, KR매출 70, US무료 23 | 완전 정적 (트라이픽스류) | 생명·코인 (?) |
| Solitaire - Classic / Solitaire (smilerlee) / Solitaire Verse | solitaire.patience.card.games.klondike.free / com.smilerlee.klondike / com.gimica.solitaireverse | US무료 65·127·195 | 완전 정적 | 광고 |
| Jigsort Solitaire / Jigsawcard / Jigsaw Drop | jigsolitaire.jigsort.jigsaw.solitaire.puzzle / com.oakever.jigsawcard / jigsaw.solitaire.match.games.free | KR무료 119, US무료 142·169 | 완전 정적 (?) | 광고 |
| Domino Dreams | com.screenshake.dominodreams | KR매출 114 | 완전 정적 (?) | 생명 (?) |
| 고스톱M (오프라인 맞고) | com.noriworks.gm | KR무료 8 | 완전 정적 (AI 상대 (?)) | 광고 |
| 국민 고스톱 | com.noriworks.kg | KR무료 21 | 완전 정적 (?) | 광고 |
| 한게임 신맞고 | com.NHNEnt.NDuelgo | KR무료 179, KR매출 74 | 대체로 정적 (온라인 대전에 턴 타이머) | 계정, 온라인, 사람과 대전 |
| 피망 뉴맞고 | com.neowiz.games.newmatgo | KR매출 79 | 대체로 정적 (턴 타이머) | 계정, 온라인 |
| 한게임 섯다&맞고 | com.NHNEnt.MSudda | KR매출 50 | 대체로 정적 (턴 타이머) | 계정, 온라인 |
| 한게임 포커 / 한게임포커 클래식 / 한게임 로얄 홀덤 | com.nhnent.Qpoker / com.nhnent.pokerclassic / com.nhn.AApoker | KR매출 19·20·94, KR무료 144(클래식) | 대체로 정적 (턴 타이머) | 계정, 온라인, 사람과 대전, 사행성 장르 |
| 피망 포커 / WPL 텍사스 홀덤 | com.neowiz.games.poker / com.zempot.wplpoker | KR매출 48·92 | 대체로 정적 (턴 타이머) | 계정, 온라인 |
| 루미큐브 (Rummikub) | com.rummikubfree | KR무료 123 | 대체로 정적 (온라인 턴 타이머. 혼자 하는 모드는 확인 못 함) | 온라인 |
| UNO! / Spades | com.matteljv.uno / com.tripledot.spades | US무료 173·81 | 대체로 정적 (UNO는 온라인 턴 타이머, Spades는 봇 상대 (?)) | 온라인(UNO) |
| 체스닷컴 (Chess - Play and Learn) | com.chess | KR무료 133, US무료 19 | 완전 정적 (봇·퍼즐·통신 대국). 실시간 대국은 시계가 있음 | 계정, 실시간 모드 |
| Chess - Offline Board Game | com.gamovation.chessclubpilot | US무료 144 | 완전 정적 | 광고 |
| 모노폴리 고 (MONOPOLY GO!) | com.scopely.monopolygo | US무료 36 | 완전 정적 (주사위 굴리기) | 주사위 수 제한, 계정 권유 |
| 코인 마스터 (Coin Master) | com.moonactive.coinmaster / com.moonactive.cmboard | KR매출 137(cmboard), US무료 107·113 | 완전 정적 | 스핀 제한, 계정 |
| 다이스 드림즈 (Dice Dreams) | com.superplaystudios.dicedreams | KR매출 168, US무료 88 | 완전 정적 | 주사위 제한 |
| Farkle / Bingo Dice류 | com.greatplay.farklecash / com.funcraft.farkle / com.funcraft.bingodice / com.greatplay.bingocash | US무료 30·119·136·111 | 대체로 정적 (?) (현금 대전형은 비동기 대결) | 현금 내기 앱이라 실측 대상에서 빼는 것이 맞음 |
| MONOPOLY: Bingo! | com.clipwiregames.monopolybingo | US무료 102 | 애매 (빙고는 번호가 시간에 맞춰 불림) | 시간 압박 |

## 9. 머지

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| 가십 하버 (Gossip Harbor) | com.mergegames.gossipharbor | KR무료 11, KR매출 8, US무료 5 | 완전 정적 | 에너지, 판 위 아이템이 많음 |
| 테이스티 트래블 (Tasty Travels) | com.fatmerge.global | KR무료 45, KR매출 26, US무료 100 | 완전 정적 | 에너지 |
| Foodstars / Merge Cooktopia / Merge Cooking / Flambé | com.merge.game.foodstars / com.davincinote.twomergesimul / com.merge.cooking.theme.restaurant.food / com.cola.game | KR무료 62·98·117, KR매출 98·117·58, US무료 84 | 완전 정적 (?) | 에너지 |
| Mystery Town / Hollywood Merge / Whisper Castle / Frost Valley | com.cedargames.mysterytown / com.hollywood.merge / com.mf.coffee / com.liteorange.hometopia | KR무료 70·74·81·151, KR매출 51·47, US무료 95·128·161·156 | 완전 정적 | 에너지 |
| Seaside Escape / Hotel Legacy / Merge Restyle / Family Farm Merge / Merge Teahouse | com.gamedots.seasideescape / com.fatr.global / com.play.merge.restyle / com.roomperfect.global / com.mhclyxpb.gatja | KR매출 30·96·138·154·100, KR무료 199·167·138·174 | 완전 정적 | 에너지 |
| 트래블 타운 (Travel Town) | io.randomco.travel | KR매출 82 | 완전 정적 | 에너지 |
| Overgeared Hero: Merge RPG | com.fiveminlab.overgeared.hero.backpack.merge | KR무료 110, KR매출 97 | 대체로 정적 (배치·머지는 정적, 전투는 자동) | 알 수 없음 |

## 10. 경영·타이쿤·농장·육성 시뮬

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| 타운십 (Township) | com.playrix.township | KR무료 99, KR매출 56, US무료 22 | 완전 정적 (생산 타이머만 있음. 매치3 미니게임 포함) | 계정, 메뉴가 깊음 |
| 패밀리 아일랜드 (Family Island) | com.MelsoftGames.FamilyIslandFarm | KR매출 141 | 완전 정적 | 에너지 |
| Supermarket Tycoon Empire / Frog Matcha Cafe / Little Farm Story / Dino Dig Tycoon | com.supermarkettycoon.idle / com.fm.frogcafe / com.h8games.littlefarmstory / com.pdd.sfungames | KR무료 84·195, US무료 171·177 | 완전 정적 (방치 타이쿤) | 광고 |
| Viral Streamer / Gym Idle Clicker | com.cscmobi.livestream.idle.clicker / com.uc.gym.clicker | KR무료 76, US무료 170·101 | 대체로 정적 (클리커라 연타하면 유리함) | 광고 |
| 마이 시크 몰 | com.tomato.and.ssbhckr | KR무료 28, KR매출 186 | 완전 정적 (?) (방치 경영으로 추정) | 알 수 없음 |
| Only I Get Rich: Life Rewind | com.seizemode.up | KR무료 72 | 완전 정적 (?) (선택형 인생 시뮬로 추정) | 알 수 없음 |
| 더 루키: 고교야구선수 키우기 | com.therookie.game | KR무료 185 | 대체로 정적 (?) (육성이 중심) | 알 수 없음 |
| 17-0 Football Manager | com.funcraft.profootballmanager | US무료 110 | 완전 정적 (?) | 알 수 없음 |
| My Football Hero / Rising Fighter (커리어 시뮬) | com.eonku.myfootballhero / com.risingfighter.app | KR무료 173·140 | 애매 (?) (경기 부분이 수동 조작인지 확인 못 함) | 알 수 없음 |
| 우마무스메 프리티 더비 | com.kakaogames.umamusume | KR매출 104 | 완전 정적 (육성은 선택형, 레이스는 관전) | 계정, 무거운 3D, 연출이 긺 |
| 치이카와 포켓 (Chiikawa Pocket) | jp.co.applibot.chiikawapocketgl | KR무료 16, KR매출 179 | 애매 (수집·꾸미기 중심, 미니게임 일부는 실시간 (?)) | 계정 |

## 11. 방치형(idle) RPG ("키우기")

모두 **완전 정적 또는 대체로 정적**으로 봅니다. 전투는 자동으로 흐르고 손이 급하지 않습니다. 공통 걸림돌은 다음과 같습니다.

- 화면을 덮는 팝업과 상점 유도가 아주 많습니다.
- 온라인 연결과 계정(게스트 가능 여부는 게임마다 다름)이 필요합니다.
- 결정은 대부분 "강화·뽑기·스킬 버튼"입니다. 그래서 에이전트 시험장으로서는 판단할 거리가 적습니다.

| 게임 | 패키지 | 순위 근거 | 정적 판정 |
|---|---|---|---|
| 세계수의 노래: AFK | com.end9games.worldtreeggl | KR무료 1 | 완전 정적 (?) |
| 배고파용 키우기 | com.c3wavestudio.yumyumdragon | KR무료 2 | 완전 정적 |
| 드래곤 나이트 키우기 | com.ryonggame.dragonknightidle | KR무료 35, KR매출 44 | 완전 정적 |
| 메이플스토리: 아이들 RPG | com.nexon.ma | KR무료 58, KR매출 7 | 완전 정적 |
| 소드 x 스태프 | com.zjcs.android.kr | KR무료 14, KR매출 17 | 완전 정적 (?) |
| 전생했더니 검 한 자루 | com.ymjx.gpkr | KR무료 46, KR매출 106 | 완전 정적 (?) |
| Champion Forge / Forge Master | com.mobibrain.championforge / com.hariwn.legendofcivilizations | KR무료 32, KR매출 110 | 완전 정적 |
| Top Force | com.project.idle.cd | KR무료 102, KR매출 49, US무료 143 | 완전 정적 (?) |
| 히어로즈 테일즈2 | com.tg.ysczg.kr | KR무료 122, KR매출 77 | 완전 정적 (?) |
| 슬레이어 키우기 (Slayer Legend) | com.gear2.growslayer | KR매출 85 | 완전 정적 |
| 버섯커 키우기 | com.mxdzzkr.google | KR매출 123 | 완전 정적 |
| 갓깨비 키우기 / 추노 키우기 / 나만 SSS급 퇴마사 | com.zzsjkr.google / com.dwgames.chunorpg / com.fmkr.az | KR매출 122·198·111 | 완전 정적 (?) |
| Sword! Bow! Staff!: LELE / Cat Hero / Soul Hunter Idle / Butcher Hero | com.gear2.letsgolegends / net.gameduo.gv / com.ryonggame.soulhunteridle / com.multicastgames.butcher | KR매출 105·157·170·135 | 완전 정적 |
| 쿠키런: 크럼블 (CookieRun: Crumble) | com.devsisters.cc | KR무료 156, KR매출 88 | 완전 정적 (?) |
| 스톤에이지 키우기 (StoneAge: Idle) | com.netmarble.stonkey | KR매출 177 | 완전 정적 |
| 판타지포유 / 후더덕 서바이벌 / 행콕과 함께 항해하는 나날 / 에버나이트: 리버스 / Junk Hero / Dragon Traveler | com.gnisoft.f4u.googlePlay / com.mrtgd2kr.google / com.hzjmtyi.krg1 / com.yyjlkr.az / com.superpixel.scrap / com.gametree.lhlr.gp | KR무료 7·71·121·9, KR매출 124·61·196·156 | 완전 정적 (?) (방치형으로 추정. 장르를 확인하지 않음) |

## 12. 수집형 RPG·턴제·덱빌딩·카드 배틀·전략

| 게임 | 패키지 | 순위 근거 | 정적 판정 | 걸림돌 |
|---|---|---|---|---|
| 붕괴: 스타레일 | com.HoYoverse.hkrpgoversea | KR매출 2 | 대체로 정적. 전투는 턴제로 기다려 줌. 필드 탐색은 3D 이동 조작 | 무거운 3D, 계정, 대용량 다운로드 |
| 림버스 컴퍼니 | com.ProjectMoon.LimbusCompany | KR매출 10 | 완전 정적 (턴제) | 계정, 메뉴가 복잡함 |
| 카오스 제로 나이트메어 | com.smilegate.chaoszero.stove.google | KR매출 24 | 완전 정적 (덱빌딩 로그라이트 (?)) | 스토브 계정 |
| 세븐나이츠 리버스 (Seven Knights Re:BIRTH) | com.netmarble.tskgb | KR매출 45 | 대체로 정적 (턴제 수집형, 자동 전투) | 계정 |
| 서머너즈 워 | com.com2us.smon.normal.freefull.google.kr.android.common | KR매출 95 | 대체로 정적 (턴제. 실시간 아레나에는 턴 타이머) | 계정, 실시간 PvP 모드 |
| 페이트/그랜드 오더 | com.netmarble.fgok | KR매출 131 | 완전 정적 (커맨드 카드 턴제) | 계정, 긴 스토리 |
| 블루 아카이브 | com.nexon.bluearchive | KR매출 55 | 대체로 정적 (전투는 실시간이나 자동 전투가 있음. EX스킬을 수동으로 쓰려면 타이밍이 필요) | 계정, 3D |
| 트릭컬 리바이브 | com.epidgames.trickcalrevive | KR매출 40 | 대체로 정적 (자동 전투) | 계정 |
| 에픽세븐 | com.stove.epic7.google | KR매출 181 | 완전 정적 (턴제. 실시간 PvP에는 타이머) | 스토브 계정 |
| 소녀전선2: 망명 | com.haoplay.game.and.exilium | KR매출 146 | 완전 정적 (턴제 전술) | 무거운 3D |
| RAID: Shadow Legends / Hero Wars | com.plarium.raidlegends / com.nexters.herowars | KR매출 165, KR무료 196 | 대체로 정적 (자동 전투) | 계정 |
| 명일방주 | com.YoStarKR.Arknights | KR매출 180 | 대체로 정적 (실시간 디펜스지만 일시정지 중에도 배치할 수 있음) | 시간 흐름 관리가 필요함 |
| 위저드리 바리안츠 다프네 | jp.co.drecom.wizardry.daphne | KR매출 182 | 대체로 정적 (턴제 전투, 3D 던전 이동) | 3D |
| 대항해시대 오리진 | com.linegames.uwogl | KR매출 183 | 대체로 정적 (?) | 무거운 3D |
| 쿠키런: 킹덤 | com.devsisters.ck | KR매출 155 | 대체로 정적 (왕국 경영은 정적, 전투는 자동 실시간이고 스킬 수동 선택) | 계정 |
| 하스스톤 | com.blizzard.wtcg.hearthstone | KR매출 164 | 대체로 정적 (턴 타이머. 솔로 모험이나 선술집 PvE도 있음) | 배틀넷 계정, 온라인 |
| 유희왕 마스터 듀얼 | jp.konami.masterduel | KR매출 128 | 대체로 정적 (PvP 턴 타이머. 솔로 모드 있음) | 계정, 연출이 긺 |
| 포켓몬 카드 게임 포켓 | jp.pokemon.pokemontcgp | US무료 124 (KR은 200위 안에 없음) | 대체로 정적 (대전 턴 타이머. 팩 개봉·솔로전은 정적) | 계정 |
| The Bazaar | com.tempogames.thebazaar | KR무료 29, US무료 12 | 대체로 정적 (상점·배치는 정적, 전투는 자동 관전) | 계정, 비동기 PvP |
| 냥코 대전쟁 | jp.co.ponos.battlecatskr | KR매출 62, KR무료 126 | 애매 (실시간 라인 디펜스. 템포가 느리지만 생산 타이밍이 승패에 영향) | 없음 |

## 13. 4X·SLG (판정: 애매)

**판정 이유**: 도시 건설, 연구 대기열, 영웅 육성은 완전 정적입니다. 하지만 연맹 집결, 공격과 방어, 시즌 전쟁은 시간과 다른 플레이어에 묶여 있습니다. 또 광고에 나오는 "미니게임 레벨"(퍼즐·러너)은 게임마다 실시간일 수 있습니다. 한 판을 이기는 목표가 아니라 계속 운영하는 게임이어서 평가 기준을 세우기도 어렵습니다.

| 게임 | 패키지 | 순위 근거 | 걸림돌 |
|---|---|---|---|
| 화이트아웃 서바이벌 | com.gof.global | KR무료 51, KR매출 5, US무료 80 | 온라인, 계정, 연맹 이벤트 |
| 킹샷 (Kingshot) | com.run.tower.defense | KR무료 91, KR매출 9, US무료 187 | 같음 |
| 라스트 워 | com.fun.lastwar.gp | KR매출 23 | 같음. 광고 속 미니게임은 실시간 |
| Top Lords / Lordrush / Last Asylum / Tiles Survive! | com.gamespark.topking.gp / com.s01.global / com.phs.global / com.funplus.ts.global | KR무료 15·96·37·55, KR매출 18·53·33·67 | 같음 |
| 라이즈 오브 킹덤즈 / 삼국지 전략판 / Evony / State of Survival / Dark War Survival / Doomsday / Top Heroes / Total Battle | 차트의 패키지 그대로 | KR매출 129·109·200·136·107·195·125, US무료 182(Total Battle) | 같음 |
| 퍼즐 오브 Z | com.global.ztmslgkr | KR매출 145 | 매치3와 SLG를 섞은 게임. 퍼즐 부분은 정적 (?) |
| Wild Water World | com.iyagames.flotshelter | KR무료 63, KR매출 35 | 장르 미확인 (생존 SLG로 추정) |
| 클래시 오브 클랜 | com.supercell.clashofclans | KR매출 99 | 건설은 정적. 공격은 제한 시간 3분 안에 실시간으로 배치해야 함 |

---

## 차트에 있었지만 정적이 아니라고 빼거나 애매로 둔 주요 게임

- **승리의 여신: 니케** (KR매출 4, KR무료 149): 애매. 기본 전투는 조준 사격이고, 자동 조준 모드로 밀리는 스테이지가 많습니다. 자동 모드로 돌릴 수는 있어도 수동으로 둘 수는 없는 게임이어서 "정적 게임"으로 보지 않았습니다.
- **명조, 젠레스 존 제로, 원신, NTE, 몬길: 스타 다이브(?)**: 실시간 액션 RPG입니다.
- **리니지M·W·2M, 오딘, 로드나인, 나이트 크로우, 레이븐2, AION2, 마비노기 모바일, 미르의 전설2, 열혈강호, 뮤 모나크, 로그W, SOL: enchant, 이클립스(?)**: 자동 사냥 MMORPG입니다. 손은 한가하지만 오픈필드에서 실시간 PvP가 일어나고 3D가 무겁습니다. 그래서 정적 게임으로 보지 않았습니다(애매에 가까운 제외).
- **도깨비의세계** (KR매출 1, KR무료 94): 장르를 확인하지 못해 판정을 보류했습니다.
- **Lucky Defense, Coop TD, 타워 디펜스 온라인, Random Dice 2, Bunker TapTap Defense, Galaxy Defense**: 실시간 협동 디펜스입니다. 웨이브 시간에 맞춰 소환해야 합니다.
- **Roblox**: 플랫폼이고, 대부분 실시간 3D입니다.
- **Pokémon GO**: 위치 기반 AR입니다.
- **브롤스타즈, 클래시 로얄, 와일드 리프트, 모바일 레전드, PUBG (Flash 포함), Fortnite, Free Fire, Warline, Last Z, Ghost Sniper, Hunting Sniper**: 실시간 대전 또는 슈팅입니다.
- **FC 모바일, FC 온라인 M, eFootball, EA FC, 컴투스프로야구, MLB 9이닝스, 프로야구GO!, Total Football**: 스포츠 경기를 실시간으로 조작합니다. 감독 모드는 예외일 수 있습니다.
- **Marmot Survivor, Survivor: Ace, Idle Cat Gunner, Mythic Survivors, Cell Survivor, Zombie Waves, XP Hero, 1945 Air Force**: 뱀서류(서바이버류)와 슈팅입니다.
- **Geometry Dash Lite, Infinite Stairs, Subway Surfers, Sled Surfers, 쿠키런: 오븐브레이크, 카트라이더 러쉬+, Pocket Champs, RUSH: Xtreme, Cat Jump, Bottle Flip 3D, Aquapark.io**: 러너, 레이싱, 타이밍 게임입니다.
- **Magic Tiles 3, Music Piano 7, Magic Level 9 Music Piano, Easy Drum**: 리듬 게임입니다.
- **Paper.io 2, Hole.io, Snake.io, Snake Clash!, Mob Control, Bulldozer Master, Auto Drill, Tiny Ride, Steal Eggs, Shoot & Hide**: 실시간 하이퍼캐주얼입니다.
- **8 Ball Pool** (US무료 92): 애매. 턴제지만 샷 하나하나가 조준·힘 조절이고, 샷 타이머가 있습니다.
- **Tank Busters, Royal Smash!, Castle Busters, Angry Smash, Coin Bounce, Break Bricks** (?): 물리·조준형으로 추정해 뺐습니다. 턴제 포격이라면 대체로 정적일 수 있으니 확인이 필요합니다.
- **ASMR·힐링 시뮬** (Clicker DIY, Tanghulu Master, Skincare Time, Clean Perfect OCD, Surprise Dumpling, Fidget Trading, Joystick Makeover, Satisfying Toys, Mini Games 모음류): 입력을 기다려 주는 것이 많지만 승패·목표가 약하고, 미니게임 모음은 실시간 게임이 섞여 있습니다. 애매로 두었습니다.
- **Block Craft 3D, Block World, Melon Sandbox, Toca Boca World**: 샌드박스이고 목표가 없습니다.
- **Heartopia, 러브앤딥스페이스, hololive Dreams, Top Girl**: Heartopia는 3D 라이프 시뮬, 러브앤딥스페이스는 스토리는 정적이지만 전투는 액션입니다. hololive Dreams와 Top Girl은 장르를 확인하지 못했습니다. 판정을 보류했습니다.

## 요청 목록 가운데 2026-11-15 차트 3종 200위 안에서 찾지 못한 게임

아래 게임은 이번에 확인한 세 차트에 없었습니다. 순위와 다운로드 수는 확인하지 못했습니다. 인지도로는 상위권에 있었던 게임이지만, **지금 200위 안에 든다고 말할 근거는 없습니다**. 정적 판정은 일반 지식에 근거합니다.

| 게임 | 확인한 것 | 정적 판정 (추정) |
|---|---|---|
| 애니팡 매치라이크 | 2024-09 출시 다음 날 한국 구글플레이 인기 1위(쿠키뉴스 기사). 이번 차트에는 없음 | 대체로 정적 (매치3에 로그라이크를 섞음) |
| 캔디크러쉬 소다 | Play 페이지는 있으나 다운로드 수는 읽지 못함 | 완전 정적 |
| 프렌즈팝 / 프렌즈타운 | 차트에 없음 (프렌즈팝콘만 KR매출 167) | 완전 정적 |
| 포코팡, 쿠키런: 퍼즐 월드 | 차트에 없음 | 포코팡은 시간제 스테이지가 기본이어서 대체로 정적. 퍼즐 월드는 완전 정적 |
| 머지 맨션 | 차트에 없음. 추정한 패키지(com.gramgames.mergemansion)의 Play 페이지가 404였음. 제작사 지원 페이지에는 1.5GB RAM 이하 안드로이드 지원 중단(2025-08) 공지만 있음. 현재 Play 판매 상태는 확인 못 함 | 완전 정적 |
| 2048 원조류 | 차트에는 2248·수박게임류만 있음 | 완전 정적 |
| Triple Match 3D | 차트에 없음 (Goods Triple Match 등 비슷한 게임은 있음) | 대체로 정적 (타이머) |
| 한게임 바둑·장기, 피망 바둑 | 차트에 없음 (한게임·피망은 고스톱·포커만 200위 안) | 완전 정적 또는 대체로 정적 (대국 시계) |
| 세븐나이츠 키우기 | 차트에 없음 (세븐나이츠 리버스는 KR매출 45, 다른 게임임) | 완전 정적 |
| 리버스: 1999 | 차트에 없음 | 완전 정적 (카드 턴제) |
| 마블 스냅 | 차트에 없음 | 대체로 정적 (턴 타이머, 실시간 PvP만 있음) |
| 문명류 (Civilization) | 차트에 없음 | 완전 정적 |
| 헤이데이, 심시티 빌드잇 | 차트에 없음 | 완전 정적 |
| 우드 블록 퍼즐 원조, Tile Explorer 외 타일류 | Woodoku Blast US무료 69, Tile Explorer US무료 118만 확인 | 완전 정적 |

## 확신이 낮은 항목

1. 순위 숫자 자체: 요약 모델이 옮긴 것이어서 몇 위 정도의 오차나 누락이 있을 수 있습니다. 미국 24위가 빠진 것이 그 예입니다. 중요한 게임은 AppBrain 원문에서 다시 확인하기를 권합니다.
2. 이름만 보고 장르를 추정한 하이퍼캐주얼 퍼즐: Colony Flow, Food Hunt, Pixel Flow, Hole Stars, Egg Journey, Cube Land, Stack Smash, Maze Escape 등입니다.
3. 레벨 타이머가 있는지: 버스 잼, 나사, 3D 트리플 매치, 틀린그림찾기 계열은 "타이머가 흔하다"는 일반 지식으로만 판정했습니다.
4. 방치형으로 추정한 한국·중국 신작 (11장 마지막 줄): 판타지포유, 후더덕 서바이벌, 행콕과 함께 항해하는 나날, 에버나이트, Junk Hero, Dragon Traveler입니다. 일부는 MMORPG에 가까울 수 있습니다.
5. 고스톱M·국민 고스톱이 AI 상대인지: "데이터 필요없는"이라는 제목만 보고 오프라인 AI 대전으로 추정했습니다.
6. 걸림돌 열 전체: 생명·에너지·계정 정보는 일반 지식이고, 이번에 확인하지 않았습니다.

## 출처

- AppBrain, Google Play Top Free Games South Korea, 1–100 / 101–200 (2026-11-15): https://www.appbrain.com/stats/google-play-rankings/top_free/game/kr , https://www.appbrain.com/stats/google-play-rankings/top_free/game/kr/2
- AppBrain, Google Play Top Grossing Games South Korea, 1–100 / 101–200 (2026-11-15): https://www.appbrain.com/stats/google-play-rankings/top_grossing/game/kr , https://www.appbrain.com/stats/google-play-rankings/top_grossing/game/kr/2
- AppBrain, Google Play Top Free Games United States, 1–100 / 101–200 (2026-11-15): https://www.appbrain.com/stats/google-play-rankings/top_free/game/us , https://www.appbrain.com/stats/google-play-rankings/top_free/game/us/2
- 애니팡 매치라이크 출시 직후 인기 1위 기사 (쿠키뉴스, 2024-09-05): https://www.kukinews.com/article/view/kuk202409050244
- 머지 맨션 저사양 안드로이드 지원 중단 안내 (Metacore 지원 페이지): https://metacore.theymes.com/hc/en/merge-mansion/articles/why-cant-i-download-and-play-merge-mansion-543
