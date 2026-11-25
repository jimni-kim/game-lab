// 게임 플러그인 소개 자료 (ARMY 신규 기능 제안). node build.js <out.pptx>
const path = require("path");
const fs = require("fs");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");
const { applyTheme } = require("/Users/jimni/.claude/skills/synced/d517ae71-3f23-4943-a557-fb37e1970ac9_4ffa7be5-c01f-4d0f-b07c-280bb29c9345/pptx/scripts/apply_theme.js");

const OUT = process.argv[2] || "deck.pptx";
const VID = "/Users/jimni/AgenticFW/game-lab/out/demo-video/final-demo-videos";
const POSTER = path.join(__dirname, "posters");
const CONFLUENCE_URL = "https://confluence.sec.samsung.net/spaces/SODA/pages/2393725331/Game+Plugin";

const THEME = {
  name: "Game Plugin",
  headFontFace: "SamsungOneKorean",
  bodyFontFace: "SamsungOneKorean",
  colors: {
    dk1: "1B1B2F", lt1: "FFFFFF", dk2: "18203F", lt2: "F2F4F9",
    accent1: "FF6B6B", // coral: 강조, 안 됨
    accent2: "17A398", // teal: 코드
    accent3: "E9963E", // amber: 모델
    accent4: "5B6787", // slate: 보조 글
    accent5: "E3E7F2", // pale: 카드 테두리
    accent6: "7C5CFF", // violet: 실시간
    hlink: "2F6FEB", folHlink: "7C5CFF",
  },
};
const H = THEME.colors; // hex (배경·아이콘처럼 hex만 받는 곳)

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "게임 플러그인";
const C = pres.SchemeColor;

// ---------- icons ----------
async function icon(name, hex, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(fa[name], { color: "#" + hex, size: String(size) }));
  const buf = await sharp(Buffer.from(svg)).resize(size, size).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}
function poster(n) {
  return "image/jpeg;base64," + fs.readFileSync(path.join(POSTER, n + ".jpg")).toString("base64");
}

// ---------- layouts ----------
const FOOT = "게임 플러그인 · ARMY 신규 기능 제안";
pres.defineSlideMaster({
  title: "TITLE",
  background: { color: H.dk2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 1.7, w: 6.6, h: 1.3, fontSize: 54, bold: true, color: C.background1, valign: "bottom", align: "left", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 3.15, w: 6.6, h: 1.5, fontSize: 22, color: C.accent5, valign: "top", align: "left", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "SECTION",
  background: { color: H.dk2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.45, w: 12.1, h: 0.85, fontSize: 32, bold: true, color: C.background1, valign: "middle", align: "left", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.6, y: 1.3, w: 12.1, h: 0.5, fontSize: 16, color: C.accent5, valign: "top", align: "left", margin: 0 }, text: "" } },
  ],
  slideNumber: { x: 12.3, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: H.accent5, align: "right" },
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: H.lt1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.4, w: 12.1, h: 0.85, fontSize: 32, bold: true, color: C.text2, valign: "middle", align: "left", margin: 0 }, text: "" } },
    { text: { text: FOOT, options: { x: 0.6, y: 7.0, w: 6, h: 0.3, fontSize: 10, color: H.accent4, margin: 0 } } },
  ],
  slideNumber: { x: 12.3, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: H.accent4, align: "right" },
});
pres.defineSlideMaster({
  title: "VIDEO",
  background: { color: H.lt2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 4.5, y: 1.2, w: 8.2, h: 0.85, fontSize: 32, bold: true, color: C.text2, valign: "middle", align: "left", margin: 0 }, text: "" } },
    { text: { text: FOOT, options: { x: 4.5, y: 7.0, w: 6, h: 0.3, fontSize: 10, color: H.accent4, margin: 0 } } },
  ],
  slideNumber: { x: 12.3, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: H.accent4, align: "right" },
});

// ---------- helpers ----------
function card(s, x, y, w, h, fill, name, line) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: 0.12, fill: { color: fill || C.background2 },
    line: line || { color: fill || C.background2, width: 0.75 }, objectName: name,
  });
}
async function iconCircle(s, name, x, y, d, circleColor, iconHex, objName) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: circleColor }, line: { color: circleColor, width: 0.5 }, objectName: objName + " circle" });
  const pad = d * 0.25;
  s.addImage({ data: await icon(name, iconHex), x: x + pad, y: y + pad, w: d - 2 * pad, h: d - 2 * pad, objectName: objName + " icon" });
}
function text(s, t, o) {
  s.addText(t, Object.assign({ isTextBox: true, margin: 0, color: C.text1, fontSize: 15, valign: "top" }, o));
}
function bullets(items, size = 15, color) {
  return items.map((it, i) => {
    const o = typeof it === "string" ? { text: it } : it;
    return { text: o.text, options: Object.assign({ bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: 6, fontSize: size, color: color || C.text1 }, o.options || {}) };
  });
}
function chip(s, x, y, w, t, fill, name) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h: 0.4, rectRadius: 0.2, fill: { color: fill }, line: { color: fill, width: 0.5 }, objectName: name });
  text(s, t, { x, y, w, h: 0.4, fontSize: 13, bold: true, color: C.background1, align: "center", valign: "middle", objectName: name + " text" });
}

(async () => {
  // =========================== 인트로 ===========================
  pres.addSection({ title: "인트로" });

  // S1 제목
  {
    const s = pres.addSlide({ masterName: "TITLE", sectionTitle: "인트로" });
    s.addText("게임 플러그인", { placeholder: "title" });
    s.addText("처음 보는 게임도 화면만 보고,\n말로 맡기면 끝까지 두는 에이전트", { placeholder: "body" });
    text(s, "ARMY 신규 기능 제안  ·  [발표자]  ·  2026-11", { x: 0.8, y: 5.3, w: 6.6, h: 0.4, fontSize: 14, color: C.accent4, objectName: "byline" });
    const ps = ["1-2048", "3-candycrush", "7-tetris", "9-rogue"];
    for (let i = 0; i < ps.length; i++) {
      const x = 7.9 + i * 1.22, y = i % 2 ? 2.35 : 1.75, w = 1.1, h = 2.44;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x - 0.06, y: y - 0.06, w: w + 0.12, h: h + 0.12, rectRadius: 0.08, fill: { color: C.background1 }, line: { color: C.background1, width: 0.5 }, objectName: "poster frame " + i });
      s.addImage({ data: poster(ps[i]), x, y, w, h, objectName: "poster " + ps[i] });
    }
    s.addNotes("한 줄로 소개: 게임 플러그인은 말로 맡기면 웹·Android 게임을 화면만 보고 두고 결과를 돌려주는 에이전트다. 오늘은 왜 필요한지, 어떻게 생겼는지, 실제로 어디까지 되는지를 보여 주고 품평을 받으려 한다.");
  }

  // S2 왜: 사용자 가치
  {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "인트로" });
    s.addText("게임 플러그인의 구현 목적", { placeholder: "title" });
    text(s, "막힌 판과 반복 작업을 도와 사용자가 게임을 계속 즐길 수 있게 한다", { x: 0.6, y: 1.3, w: 12.1, h: 0.5, fontSize: 20, bold: true, color: C.accent1, objectName: "answer" });

    const cards = [
      { x: 0.6, icon: "FaLock", head: "막힌 레벨", items: [
        "캔디크러쉬 사가: 어려운 레벨에 며칠째 막힘. 하트가 떨어지면 기다리거나 결제해야 다시 도전",
        "꿈의 집: 퍼즐을 깨야 집 꾸미기와 이야기를 이어갈 수 있음",
      ] },
      { x: 6.78, icon: "FaRotate", head: "지루한 반복", items: [
        "일일 출석·보상 받기: 매일 같은 순서로 누르는 판 밖 작업",
        "농장·방치형 게임의 자원 수집과 모바일 RPG의 반복 사냥·소탕",
        { text: "한 번 찾은 길은 코드가 기억해 다시 한다", options: { bold: true, color: C.accent1 } },
      ] },
    ];
    for (const c of cards) {
      const y = 2.0, w = 5.95, h = 3.4;
      card(s, c.x, y, w, h, C.background2, c.head + " card");
      await iconCircle(s, c.icon, c.x + 0.3, y + 0.3, 0.6, C.accent1, H.lt1, c.head);
      text(s, c.head, { x: c.x + 1.1, y: y + 0.3, w: w - 1.4, h: 0.6, fontSize: 22, bold: true, color: C.text2, valign: "middle", objectName: c.head + " head" });
      s.addText(bullets(c.items, 15), { isTextBox: true, x: c.x + 0.3, y: y + 1.1, w: w - 0.6, h: 2.2, margin: 0, valign: "top", objectName: c.head + " list" });
    }
    card(s, 0.6, 5.65, 12.13, 0.6, C.background1, "ask person", { color: C.accent5, width: 1 });
    await iconCircle(s, "FaComments", 0.75, 5.73, 0.44, C.accent2, H.lt1, "ask");
    text(s, [
      { text: "사람이 정할 곳은 사람에게  ", options: { bold: true, color: C.text2 } },
      { text: "되돌릴 수 없는 선택은 묻고, 도는 중에도 \"어디까지 했어?\" · \"멈춰\"를 말할 수 있다", options: { color: C.text1 } },
    ], { x: 1.35, y: 5.65, w: 11.2, h: 0.6, fontSize: 15, valign: "middle", objectName: "ask text" });
    s.addNotes([
      "막힌 판과 반복 작업에서 도움을 받아 사용자가 즐기려던 게임을 계속할 수 있게 한다.",
      "막힌 레벨을 맡기면 에이전트가 현재 판과 목표를 파악해 이어서 플레이한다. 사용자가 요청하면 처음부터 시도할 수도 있다.",
      "반복: 일일 보상·자원 모으기처럼 판 밖의 일은 '작업'으로 맡기고, 한 번 찾은 길은 루틴으로 저장해 코드가 다시 누른다.",
      "캔디크러쉬의 하트 충전 시간 같은 숫자를 말할 때는 발표 전에 확인할 것.",
    ].join("\n"));
  }

  // S3 기존 게임 에이전트
  {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "인트로" });
    s.addText("기존 게임 에이전트 비교", { placeholder: "title" });
    const hd = (t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.text2 } } });
    const name = (t) => ({ text: t, options: { bold: true } });
    const rows = [
      [hd("에이전트"), hd("대상 게임 및 실행 환경"), hd("관찰 입력"), hd("한계")],
      [name("GamingAgent"), "2048, 테트리스, 소코반, 포켓몬 레드 등\n(PC·에뮬레이터, 별도 웹 3매치 예시)", "화면 또는 게임이 제공하는\n텍스트 상태", "새 게임마다 환경 어댑터와 설정 필요"],
      [name("Orak"), "스타크래프트 II, Slay the Spire, 마리오 등\n(PC·에뮬레이터·웹, 총 12종)", "게임별 인터페이스의\n텍스트 상태 또는 화면", "등록된 12종에 맞춘 평가용 벤치마크"],
      [name("Cradle"), "Red Dead Redemption 2, Stardew Valley,\nCities: Skylines 등 (PC 중심)", "화면·영상", "새 게임마다 UI 제어, 스킬, 프롬프트 연결 필요"],
      [name("Voyager"), "마인크래프트 (Java·Mineflayer)", "위치·인벤토리 등 API의\n구조화된 상태", "마인크래프트만 지원"],
      [name("VPT"), "마인크래프트 (MineRL)", "게임 시점 영상", "마인크래프트만 지원. 다른 게임은 별도 학습 필요"],
      [name("GameAgent AI"), "2048, Snake, Tetris, Wordle (웹 브라우저)", "브라우저 스크린샷", "미등록 URL은 기본 키 입력·클릭으로 시도.\n규칙 이해와 종료 판정은 보장되지 않음"],
      [name("Match3Solver"), "자체 3매치 엔진, Royal Kingdom\n(Android ADB·이미지 입력·시뮬레이션)", "스크린샷에서 읽은\n보드와 블록 배열", "다른 3매치도 판독기·규칙·보드 호환 작업 필요"],
    ];
    s.addTable(rows, {
      x: 0.6, y: 1.4, w: 12.13, colW: [1.54, 4.03, 2.48, 4.08], fontSize: 13, color: C.text1, valign: "middle",
      border: { type: "solid", pt: 0.75, color: H.accent5 }, fill: { color: C.background1 }, rowH: 0.55, margin: 0.07,
      objectName: "agents table",
    });
    card(s, 0.6, 6.07, 12.13, 0.73, C.text2, "gap card");
    text(s, "공통 한계", { x: 0.83, y: 6.07, w: 2.25, h: 0.73, fontSize: 16, bold: true, color: C.accent1, valign: "middle", objectName: "gap head" });
    text(s, "조사한 사례에서는 미등록 게임에 자연어 목표를 주고,\n화면을 보며 목표까지 수행하는 흐름을 확인하지 못했다.", {
      x: 3.12, y: 6.07, w: 9.3, h: 0.73, fontSize: 15, color: C.background1, valign: "middle", objectName: "gap text",
    });
    s.addNotes([
      "비교 범위는 표에 수록한 7개 사례다. 자연어로 받은 목표를 미등록 웹·앱 게임의 화면과 실행 결과에 연결해 수행하는 전체 흐름은 조사 자료에서 확인되지 않았다.",
      "GameAgent AI는 미등록 URL도 시도한다. 기본 키 입력과 중앙 클릭을 사용하며 규칙 이해, 종료 판정, 목표 달성에는 제약이 있다. GameAgent AI는 브라우저용이고 Match3Solver의 웹·iOS 제어에는 별도 연결이 필요하다.",
      "출처: Confluence §1 구현 목적 및 §2 기존 게임 에이전트 조사. Orak 대상 게임·실행 환경·입력 유형: https://github.com/krafton-ai/Orak/blob/master/README.md",
    ].join("\n"));
  }

  // S4 블록 퍼즐 요청에 대한 에이전트별 결과
  {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "인트로" });
    s.addText("블록 퍼즐 요청에 대한 에이전트별 결과", { placeholder: "title" });
    card(s, 0.6, 1.4, 12.13, 0.7, C.background2, "request");
    text(s, [
      { text: "같은 요청  ", options: { bold: true, color: C.accent4 } },
      { text: "“xpgameplus 블록 퍼즐에서 고득점 받아줘”", options: { bold: true, color: C.text2 } },
    ], { x: 0.9, y: 1.4, w: 11.6, h: 0.7, fontSize: 18, valign: "middle", objectName: "request text" });
    const agents = [
      { name: "Pickle", ok: false, note: "drag&drop 도구 없음\n-> 도구 추가함." },
      { name: "muse", ok: true, note: "성공" },
      { name: "dots", ok: true, note: "처음 네 수는 모델이 판단\n이후 코드 풀이기로 진행" },
      { name: "army", ok: false, note: "drag&drop 도구 없음\n-> 도구 추가함.", hl: true },
    ];
    for (let i = 0; i < agents.length; i++) {
      const a = agents[i], x = 0.6 + i * 3.12, y = 2.4, w = 2.85, h = 2.55;
      card(s, x, y, w, h, a.hl ? C.background1 : C.background2, a.name + " card", a.hl ? { color: C.accent1, width: 2.25 } : undefined);
      text(s, a.name, { x: x + 0.3, y: y + 0.3, w: 1.6, h: 0.6, fontSize: 24, bold: true, color: C.text2, valign: "middle", objectName: a.name + " name" });
      await iconCircle(s, a.ok ? "FaCheck" : "FaXmark", x + w - 0.95, y + 0.25, 0.7, a.ok ? C.accent2 : C.accent1, H.lt1, a.name);
      text(s, a.note, { x: x + 0.3, y: y + 1.15, w: w - 0.6, h: 1.25, fontSize: 14, color: C.text1, objectName: a.name + " note" });
    }
    card(s, 0.6, 5.2, 7.3, 1.45, C.background2, "needs");
    text(s, "게임 요청이 요구하는 것", { x: 0.9, y: 5.32, w: 6.8, h: 0.4, fontSize: 15, bold: true, color: C.text2, objectName: "needs head" });
    const needs = ["끌기", "실시간", "수백 수 반복", "광고·팝업 넘기기"];
    let cx = 0.9;
    for (const n of needs) {
      const w = 0.45 + n.length * 0.2;
      chip(s, cx, 5.95, w, n, C.accent1, "need " + n);
      cx += w + 0.2;
    }
    card(s, 8.15, 5.2, 4.58, 1.45, C.text2, "direction");
    text(s, [
      { text: "dots에서 참고한 풀이 방식", options: { bold: true, color: C.accent1, breakLine: true } },
      { text: "- 모델이 먼저 화면을 보고 게임을 파악.", options: { color: C.background1, breakLine: true } },
      { text: "- 풀이기가 수를 계산해 자동 진행.", options: { color: C.background1 } },
    ], { x: 8.45, y: 5.3, w: 4.0, h: 1.25, fontSize: 15, valign: "middle", objectName: "direction text" });
    s.addNotes("같은 블록 퍼즐 요청을 네 에이전트에 줬다. 비교 당시 Pickle과 army는 drag&drop 도구가 없어 진행하지 못했다. 이후 도구를 추가했다. 게임 요청은 클릭 몇 번이 아니라 끌기, 실시간, 수백 수의 반복, 광고·팝업 넘기기를 요구한다. dots는 처음 네 수를 모델이 판단한 뒤 코드 풀이기로 전환했다. 플러그인도 이 방식을 참고해 모델이 먼저 게임을 파악하고, 적용 가능한 풀이기가 있으면 코드로 수를 계산해 반복적인 모델 호출을 줄인다. (비교 결과는 다시 확인할 예정)");
  }

  // S5 게임 플러그인 제안
  {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "인트로" });
    s.addText("제안: 게임 플러그인", { placeholder: "title" });
    text(s, "자연어로 목표를 받는다.\n미등록 게임도 화면에서 규칙과 조작법을 파악해 하던 판을 이어가거나 새 판을 진행한다.", { x: 0.6, y: 1.28, w: 12.1, h: 0.68, fontSize: 17, bold: true, color: C.text2, objectName: "definition" });
    const reqs = [
      { tag: "지금 진행에서 레벨 N개", t: "“캔디크러쉬 사가 레벨 3개 깨줘. 지면 다시 해도 돼”" },
      { tag: "처음 보는 게임", t: "“틱택토 한 판 이겨줘”" },
      { tag: "멈춤 조건 · 선택 방침", t: "“Rogue Adventure 새 판 시작해서 싸움 하나 이기면 멈춰줘.\n고를 때는 체력을 잃지 않는 안전한 쪽으로”" },
    ];
    let y = 2.15;
    for (const r of reqs) {
      const lines = Math.ceil(r.t.length / 30), h = 0.8 + lines * 0.3;
      card(s, 0.6, y, 7.6, h, C.background2, r.tag);
      chip(s, 0.85, y + 0.2, 0.55 + r.tag.length * 0.17, r.tag, C.accent4, "tag " + r.tag);
      text(s, r.t, { x: 0.85, y: y + 0.68, w: 7.1, h: lines * 0.3, fontSize: 16, color: C.text1, objectName: "req " + r.tag });
      y += h + 0.2;
    }
    // 대화창 모형
    card(s, 8.55, 2.15, 4.18, 3.05, C.text2, "chat");
    text(s, "데모 대화창 (eval serve)", { x: 8.85, y: 2.35, w: 3.6, h: 0.4, fontSize: 15, bold: true, color: C.background1, objectName: "chat head" });
    const bubble = (x, y, w, t, mine, n) => {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h: 0.55, rectRadius: 0.15, fill: { color: mine ? C.accent1 : C.background1 }, line: { color: mine ? C.accent1 : C.background1, width: 0.5 }, objectName: "bubble " + n });
      text(s, t, { x: x + 0.15, y, w: w - 0.3, h: 0.55, fontSize: 14, color: mine ? C.background1 : C.text1, valign: "middle", objectName: "bubble text " + n });
    };
    bubble(10.35, 2.95, 2.1, "어디까지 했어?", true, 1);
    bubble(8.85, 3.65, 3.0, "지운 줄 0개, 블록 4개", false, 2);
    bubble(11.35, 4.35, 1.1, "멈춰", true, 3);
    s.addNotes("요청은 한 문장이다. 어디서(웹 주소/설치된 앱), 무엇을(레벨 N개, 끝까지, 이기면 멈춤), 무엇을 허락하는지(지면 다시 하기), 선택의 방침까지 말로 준다. 대화 예시는 실측(PLAN §35 D2)의 실제 질문과 답이다.");
  }

  // =========================== 구조 ===========================
  pres.addSection({ title: "구조" });
  {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "구조" });
    s.addText("구조: 모델은 처음과 막힐 때만, 반복은 코드가", { placeholder: "title" });
    text(s, "게임 조사 내용과 플레이 중 파악한 정보를 저장 -> 다음 요청에서 게임을 파악하고 진행하는 데 활용", { x: 0.6, y: 1.28, w: 12.1, h: 0.65, fontSize: 16, color: C.accent4, objectName: "game memory" });
    const box = (x, y, w, h, fill, lines, n, dark = true) => {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.1, fill: { color: fill }, line: { color: fill, width: 0.5 }, objectName: n });
      s.addText(lines.map((l, i) => ({ text: l.t, options: Object.assign({ breakLine: i < lines.length - 1, fontSize: l.s || 15, bold: !!l.b, color: dark ? C.background1 : C.text1 }, l.o || {}) })),
        { isTextBox: true, x: x + 0.15, y, w: w - 0.3, h, margin: 0, valign: "middle", objectName: n + " text" });
    };
    const arrow = (x1, y1, x2, y2, n) => {
      const o = { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.max(Math.abs(x2 - x1), 0.01), h: Math.max(Math.abs(y2 - y1), 0.01), line: { color: C.accent4, width: 1.5, endArrowType: "triangle" }, objectName: n };
      if (y2 < y1) o.flipV = true;
      s.addShape(pres.shapes.LINE, o);
    };
    const cy = 4.37;
    box(0.6, cy - 0.5, 1.75, 1.0, C.text2, [{ t: "말로 요청", b: true, s: 17 }], "request");
    arrow(2.35, cy, 2.75, cy, "a1");
    box(2.75, cy - 0.75, 2.5, 1.5, C.text2, [{ t: "게임 알아보기", b: true, s: 17 }, { t: "이전 기록을 참고", s: 14 }, { t: "첫 플레이면 먼저 조사", s: 14 }], "identify");
    const br = [
      { y: 2.13, h: 1.38, fill: C.accent2, lines: [{ t: "등록 계열에 속하는 게임", b: true, s: 17 }, { t: "첫 플레이도 그 계열의 판독기와 풀이기로 진행한다", s: 14 }, { t: "2048, 지뢰찾기, 블록 놓기, 3매치, 소코반, 클론다이크", s: 12 }] },
      { y: 3.68, h: 1.38, fill: C.accent3, lines: [{ t: "적용할 계열 풀이기가 없을 때", b: true, s: 17 }, { t: "모델이 직접 두거나 규칙과 풀이를 코드로 작성한다", s: 14 }, { t: "직접 두기는 카드 전투, 스토리, 선택, 차례 판단도 처리한다", s: 12 }] },
      { y: 5.23, h: 0.94, fill: C.accent6, lines: [{ t: "떨어지는 블록의 실시간 조작", b: true, s: 17 }, { t: "코드가 초당 4~8번 판을 읽고 조작한다", s: 14 }] },
    ];
    const bx = 5.85, bw = 4.6;
    for (let i = 0; i < br.length; i++) {
      const b = br[i];
      arrow(5.25, cy, bx, b.y + b.h / 2, "branch in " + i);
      box(bx, b.y, bw, b.h, b.fill, b.lines, "branch " + i, b.fill === C.accent6);
      arrow(bx + bw, b.y + b.h / 2, 11.0, cy, "branch out " + i);
    }
    box(11.0, cy - 0.75, 1.73, 1.5, C.text2, [{ t: "돌려주기", b: true, s: 17 }, { t: "깬 레벨 · 쓴 것", s: 12 }, { t: "멈춘 화면", s: 12 }], "handback");
    s.addNotes([
      "게임별로 조사 내용과 플레이 중 파악한 정보를 보관한다. 다음 요청에서 계열 판독에 필요한 설정이나 모델이 만든 풀이 코드를 다시 쓰고, 직접 둘 때는 이전에 파악한 규칙을 참고한다. 이전 기록이 없는 게임은 스토어 설명, 위키, 게임 안 도움말 등을 조사해 처리 경로를 찾는다.",
      "실시간을 포함해 7개 계열을 등록했다. 첫 플레이에서도 등록 계열에 속한다는 것을 파악하면 해당 계열의 판독기와 풀이기를 쓴다.",
      "적용할 계열 풀이기가 없으면 모델이 화면을 보며 직접 둔다. 틱택토나 스도쿠처럼 규칙을 코드로 표현할 수 있으면 모델이 규칙과 풀이를 작성하고 다음 요청에서 재사용하기도 한다.",
      "떨어지는 블록은 모델이 처음에 판과 조작법을 알려 주고, 이후 코드가 초당 4~8번 판을 읽고 조작한다.",
    ].join("\n"));
  }

  // =========================== 데모 ===========================
  pres.addSection({ title: "데모" });
  const G = {
    A: { label: "A · 계열 판독기와 풀이기", color: C.accent2, hex: H.accent2 },
    B: { label: "B · 직접 두기와 규칙 코드", color: C.accent3, hex: H.accent3 },
    C: { label: "C · 실시간", color: C.accent6, hex: H.accent6 },
  };
  const formatTokens = (value) => value.toLocaleString("en-US");
  const tokenTotal = ({ input, output }) => formatTokens(input + output);
  // 토큰은 각 데모 실행의 Android summary.json에 기록된 model_tokens.
  const V = [
    { g: "A", file: "4-mines", name: "지뢰찾기 (Puzzles · Mines)", req: "“지뢰찾기 깨줘”",
      tokens: { input: 47082, output: 1376 }, result: ["계열 키트", "이김", "8", "64초", "12"],
      stats: [["이김", "결과"], ["8수", "입력"], ["7초", "판 위 시간"]], way: "계열 키트 · 지뢰찾기: 코드가 판을 읽고 풀이기가 둔다",
      see: "모델은 첫 화면만 보정하고, 그 뒤 판 위는 코드가 둔다", notes: "요청: 퍼즐 앱에서 Mines 끝까지. 결과: 이김, 8수, 판 위 7초, 전체 64초. 원래는 Flood 화면에서 시작해 스스로 퍼즐 목록으로 가서 Mines를 열었다(영상에서는 그 장면을 뺌)." },
    { g: "A", file: "1-2048", name: "2048", req: "“2048 최고점 깨줘.”",
      tokens: { input: 70350, output: 1791 }, result: ["계열 키트", "4096", "2,205", "2,742초", "18 (판 위 0)"],
      stats: [["4096", "가장 큰 칸"], ["2,205수", "입력"], ["0", "판 위 모델 질문"]], way: "계열 키트 · 2048: 코드가 읽고 expectimax 풀이기가 둔다",
      see: "원본 2,742초(약 46분) → 영상 55초 · 점수 49,384 · 수당 1.24초", notes: "새 판에서 끝까지 둠. 4096 도달, 점수 49,384, 2,205수, 수당 1.24초, 판 위 모델 질문 0. 새 판은 하네스의 설정 탭으로 열었다(글자 없는 ↻ 버튼은 에이전트가 아직 못 찾음)." },
    { g: "A", file: "5-sokoban", name: "소코반 (창고지기 Touch)", req: "“소코반 레벨 2개 깨줘.”",
      tokens: { input: 48876, output: 2148 }, result: ["계열 키트", "레벨 2개", "74", "279초", "11"],
      stats: [["2개 깸", "레벨"], ["74", "입력"], ["279초", "시간"]], way: "계열 키트 · 소코반: 밀기 단위 A* 탐색으로 길을 찾음",
      see: "상자를 밀 길을 코드가 미리 찾고, 한 걸음씩 옆 칸을 누른다", notes: "레벨 2개 연속, 279초. 소코반은 §36에서 더한 일곱 번째 계열이다." },
    { g: "A", file: "2-royalmatch", name: "로얄 매치", req: "“로얄 매치 레벨 1개 깨줘. 지면 다시 해도 돼”",
      tokens: { input: 57486, output: 8915 }, result: ["계열 키트(3매치)", "레벨 24", "31", "428초", "18"],
      stats: [["레벨 24", "깸"], ["31수", "입력"], ["428초", "시간"]], way: "계열 키트 · 3매치: 레벨 목표는 모델이 읽고, 수는 풀이기가",
      see: "현재 레벨인 24를 3매치 풀이기로 완료했다", notes: "현재 레벨 24를 31수, 428초에 완료했다. 3매치는 판독에 모델이 자주 들어가 수당 6~20초가 든다(STATUS §3)." },
    { g: "A", file: "3-candycrush", name: "캔디크러쉬 사가", req: "“캔디크러쉬 사가 레벨 3개 깨줘. 지면 다시 해도 돼”",
      tokens: { input: 470584, output: 65748 }, result: ["3매치 + 직접 두기", "레벨 3개", "84", "1,378초", "88"],
      stats: [["3개 깸", "레벨 9·10·11"], ["84", "입력"], ["1,378초", "시간"]], way: "계열 키트 · 3매치 + 막히면 직접 두기로 넘김",
      see: "현재 레벨 9부터 3개를 완료했다. 레벨 9는 모델이 직접 뒀고, 영상은 레벨 11이다.", notes: "레벨 9·10·11 깸, 1,378초. 레벨 9에서 키트가 33수 중 28수를 헛뒀고(물고기 사탕 맞바꿈, 젤리 판독 흔들림), 그 레벨은 모델의 직접 두기가 깼다(PLAN §36 끝). 막히면 다른 길로 넘어가는 예로 설명할 수 있다." },
    { g: "B", file: "8-tictactoe", name: "틱택토 (Tic Tac Toe XO)", req: "“틱택토 한 판 이겨줘”",
      tokens: { input: 76016, output: 1433 }, result: ["만든 계열", "이김", "4", "127초", "20"],
      stats: [["이김", "결과"], ["4", "입력"], ["127초", "시간"]], way: "만든 계열: 모델이 규칙·풀이를 코드로 씀",
      see: "규칙 코드는 앞 판(E4)에서 모델이 썼고, 이 판은 보관한 코드를 다시 쓴다", notes: "처음 보는 게임에서 모델이 규칙과 풀이를 코드로 쓴 예. 처음 쓴 판은 E4(17질문, 125초, 이김). 이 영상의 판은 그 코드를 다시 썼다. 만든 계열은 스도쿠·틱택토에서 됐고 Singles·Flood·Unruly에서는 서지 않았다." },
    { g: "B", file: "6-flood", name: "Flood (Puzzles)", req: "“Flood 한 판 깨줘.”",
      tokens: { input: 54207, output: 8040 }, result: ["직접 두기", "풂", "23", "130초", "9"],
      stats: [["풂", "결과"], ["23/25수", "입력"], ["130초", "시간"]], way: "직접 두기: 모델이 화면을 보고 입력을 고름",
      see: "그림뿐인 격자 판은 코드가 글자 판으로 정리해 모델에게 준다", notes: "직접 두기로 지정해 둠. 23/25수로 풂. 퍼즐은 사람이 목록에서 골라 줬다(여러 게임이 든 앱에서 요청한 게임으로 가는 길은 아직 없음)." },
    { g: "B", file: "9-rogue", name: "Rogue Adventure (덱 빌딩)", req: "“Rogue Adventure 새 판 시작해서 싸움 하나 이기면 멈춰줘.\n고를 때는 체력을 잃지 않는 안전한 쪽으로”",
      tokens: { input: 181799, output: 7584 }, result: ["직접 두기 + 카드 싸움", "싸움 1번 이김", "22", "236초", "26"],
      stats: [["1번 이김", "싸움"], ["62/70", "남은 체력"], ["236초", "시간"]], way: "직접 두기 + 카드 싸움: 모델이 판을 읽고, 코드가 손패 순서·대상을 탐색",
      see: "앞 작업들의 복기 교훈을 받고 시작하고, 멈춤 조건에서 스스로 멈춘다", notes: "새 판을 열고 첫 싸움을 이긴 뒤 멈춤 조건으로 멈춤. 체력 62/70, 22입력, 236초. 카드 싸움은 코드가 셀 수 있는 규칙이 아직 적어(10개 중 4개) 모델에게 넘기는 일이 많다(STATUS §3)." },
    { g: "C", file: "7-tetris", name: "Tetris®", req: "“Tetris 앱에서 다음 레벨 하나 깨줘”",
      tokens: { input: 57644, output: 2023 }, result: ["실시간 루프", "레벨 9", "108", "251초", "12"],
      stats: [["레벨 9", "깸"], ["108", "입력"], ["251초", "시간"]], way: "실시간 루프: 모델은 처음 한 번, 그 뒤 코드가 초당 4~8번 읽고 조작",
      see: "놓을 곳은 다음 조각까지 보고 풀이기(lookahead)가 고른다", notes: "레벨 9(줄 지우기 목표)를 깸, 251초. 실시간 루프에서는 판 위에서 모델을 기다리지 않는다. 테트리스 레벨 1~9를 깼다(PLAN §34, §36 끝)." },
  ];

  // 데모 지도
  {
    const s = pres.addSlide({ masterName: "SECTION", sectionTitle: "데모" });
    s.addText("데모", { placeholder: "title" });
    const widths = { A: 6.05, B: 3.87, C: 1.8 };
    let x = 0.6;
    for (const [k, w] of Object.entries(widths)) {
      const y = 1.68, h = 4.48;
      card(s, x, y, w, h, "26305A", "group " + k);
      chip(s, x + 0.2, y + 0.29, 0.5, k, G[k].color, "group chip " + k);
      text(s, G[k].label.slice(4), { x: x + 0.83, y: y + 0.29, w: w - 1.0, h: 0.4, fontSize: 15, bold: true, color: C.background1, valign: "middle", objectName: "group title " + k });
      const vs = V.filter((v) => v.g === k);
      const columnW = (w - 0.22) / vs.length;
      const posterW = 0.88;
      for (let j = 0; j < vs.length; j++) {
        const v = vs[j], left = x + 0.11 + j * columnW;
        s.addImage({ data: poster(v.file), x: left + (columnW - posterW) / 2, y: y + 1.15, w: posterW, h: 2.04, objectName: "map poster " + v.file });
        text(s, v.name.replace(/ \(.*\)/, ""), { x: left, y: y + 3.43, w: columnW, h: 0.63, fontSize: 12, align: "center", color: C.background1, objectName: "map name " + v.file });
      }
      x += w + 0.21;
    }
    s.addNotes("A는 등록 계열의 판독기와 풀이기를 쓰는 예다. 로얄 매치와 캔디크러쉬 사가에는 3매치 계열을 적용해 현재 레벨부터 요청받은 개수만큼 진행했다. B는 계열 풀이기가 없을 때 모델이 직접 두거나 규칙을 코드로 만든 예다. 틱택토 영상은 앞 판에서 만든 코드를 재사용한다. C는 떨어지는 블록의 실시간 조작 예다.");
  }

  // 영상 슬라이드
  for (const v of V) {
    const s = pres.addSlide({ masterName: "VIDEO", sectionTitle: "데모" });
    const g = G[v.g];
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.72, y: 0.32, w: 3.1, h: 6.86, rectRadius: 0.18, fill: { color: C.text2 }, line: { color: C.text2, width: 0.5 }, objectName: "phone frame" });
    if (process.env.QA) s.addImage({ data: poster(v.file), x: 0.85, y: 0.45, w: 2.84, h: 6.6, objectName: "video " + v.file });
    else s.addMedia({ type: "video", path: path.join(VID, v.file + ".mp4"), cover: "data:image/jpeg;base64," + fs.readFileSync(path.join(POSTER, v.file + ".jpg")).toString("base64"),
      x: 0.85, y: 0.45, w: 2.84, h: 6.6, objectName: "video " + v.file });
    chip(s, 4.5, 0.65, 0.5 + g.label.length * 0.17, g.label, g.color, "group chip");
    s.addText(v.name, { placeholder: "title" });
    text(s, "요청", { x: 4.5, y: 2.2, w: 1.0, h: 0.3, fontSize: 12, bold: true, color: C.accent4, objectName: "req label" });
    const rh = v.req.length > 45 ? 1.05 : 0.7;
    card(s, 4.5, 2.5, 8.23, rh, C.background1, "req box");
    text(s, v.req, { x: 4.75, y: 2.5, w: 7.8, h: rh, fontSize: 17, color: C.text2, bold: true, valign: "middle", objectName: "req text" });
    const sy = 2.5 + rh + 0.3;
    const statWidth = 1.81, statGap = 0.14;
    for (let i = 0; i < v.stats.length; i++) {
      const x = 4.5 + i * (statWidth + statGap);
      card(s, x, sy, statWidth, 1.15, C.background1, "stat " + i);
      text(s, [
        { text: v.stats[i][0], options: { fontSize: 28, bold: true, color: g.color, breakLine: true } },
        { text: v.stats[i][1], options: { fontSize: 12, color: C.accent4 } },
      ], { x: x + 0.14, y: sy + 0.05, w: statWidth - 0.28, h: 1.05, valign: "middle", objectName: "stat text " + i });
    }
    const tokenX = 4.5 + v.stats.length * (statWidth + statGap);
    const tokenWidth = 2.38;
    card(s, tokenX, sy, tokenWidth, 1.15, C.background1, "token stat");
    text(s, [
      { text: tokenTotal(v.tokens), options: { fontSize: 28, bold: true, color: g.color } },
      { text: " 토큰", options: { fontSize: 12, color: C.accent4 } },
    ], { x: tokenX + 0.14, y: sy + 0.08, w: tokenWidth - 0.28, h: 0.47, objectName: "token total" });
    text(s, `입력 토큰 ${formatTokens(v.tokens.input)}\n출력 토큰 ${formatTokens(v.tokens.output)}`, {
      x: tokenX + 0.14, y: sy + 0.58, w: tokenWidth - 0.28, h: 0.48, fontSize: 11, color: C.accent4, objectName: "token breakdown",
    });
    const wy = sy + 1.4;
    s.addImage({ data: await icon("FaRoute", g.hex), x: 4.5, y: wy + 0.04, w: 0.32, h: 0.32, objectName: "way icon" });
    text(s, v.way, { x: 4.95, y: wy, w: 7.8, h: 0.7, fontSize: 15, bold: true, color: C.text1, objectName: "way" });
    s.addImage({ data: await icon("FaMagnifyingGlass", H.accent4), x: 4.5, y: wy + 0.79, w: 0.3, h: 0.3, objectName: "see icon" });
    text(s, v.see, { x: 4.95, y: wy + 0.75, w: 7.8, h: 0.55, fontSize: 14, color: C.accent4, objectName: "see" });
    s.addNotes(v.notes);
  }

  // 결과 한눈에
  {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "데모" });
    s.addText("데모 결과 한눈에", { placeholder: "title" });
    const hd = (t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.text2 } } });
    const rows = [["묶음", "게임", "갈래", "결과", "입력", "시간", "모델 질문", "토큰 합계"].map(hd)];
    for (const v of V) {
      const numeric = (value) => ({ text: value, options: { align: "right" } });
      rows.push([
        { text: v.g, options: { bold: true, color: C.background1, fill: { color: G[v.g].color }, align: "center" } },
        v.name.replace(/ \(.*\)/, ""),
        ...v.result.slice(0, 2),
        ...v.result.slice(2).map(numeric),
        numeric(tokenTotal(v.tokens)),
      ]);
    }
    s.addTable(rows, {
      x: 0.6, y: 1.4, w: 11.9, colW: [0.70, 2.05, 2.37, 1.70, 0.90, 1.08, 1.60, 1.50], fontSize: 13, color: C.text1, valign: "middle",
      border: { type: "solid", pt: 0.75, color: H.accent5 }, fill: { color: C.background1 }, rowH: 0.46, margin: 0.06, objectName: "results table",
    });
    text(s, "모델 질문은 결과 파일(summary.json)의 전체 질문 수로, 조사·복구·판 밖 질문을 포함한다", { x: 0.6, y: 6.25, w: 12.1, h: 0.6, fontSize: 11, color: C.accent4, objectName: "results note" });
    s.addNotes("평가자는 데모보다 이 표를 오래 본다. 계열 키트는 질문이 적고, 직접 두기와 3매치는 질문이 많다. 캔디크러쉬의 88 질문은 레벨 9에서 직접 두기로 넘어간 몫이 크다.");
  }

  // =========================== 마무리 ===========================
  pres.addSection({ title: "마무리" });
  // 한계와 다음
  {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "마무리" });
    s.addText("한계와 다음", { placeholder: "title" });
    const cols = [
      { w: 2.95, head: "되는 범위", icon: "FaCheck", color: C.accent2, items: ["Android와 웹", "턴제 퍼즐, 3매치, 카드, 테트리스류(실시간)", "현 레벨부터 말로 요청"] },
      { w: 5.83, head: "덜 된 것", icon: "FaTriangleExclamation", color: C.accent3, items: ["등록 계열에 속하지 않는 게임에서 모델이 규칙과 풀이를 코드로 만드는 기능은 아직 불안정하다.", "카드 전투에서 적의 행동에 따라 달라지거나 다음 턴까지 이어지는 효과는 카드 조합을 고를 때 아직 반영하지 못한다.", "3매치에서는 실측 기준 한 수에 6~20초가 걸렸다.", "앱에서 게임을 고르거나 광고에 가려진 결과를 확인하는 등 판 밖 화면 처리에 취약하다."] },
      { w: 2.95, head: "안 되는 것", icon: "FaXmark", color: C.accent1, items: ["플랫포머·슈팅 등 반사신경 액션, 3D 게임, 리듬 게임", "테트리스류를 제외한 실시간 게임은 아직 구현하지 않았다."] },
    ];
    let x = 0.6;
    for (const c of cols) {
      const { w } = c, y = 1.45, h = 3.9;
      card(s, x, y, w, h, C.background2, c.head);
      await iconCircle(s, c.icon, x + 0.3, y + 0.3, 0.55, c.color, H.lt1, c.head);
      text(s, c.head, { x: x + 1.0, y: y + 0.3, w: w - 1.2, h: 0.55, fontSize: 20, bold: true, color: C.text2, valign: "middle", objectName: c.head + " head" });
      s.addText(bullets(c.items, 14), { isTextBox: true, x: x + 0.3, y: y + 1.1, w: w - 0.6, h: h - 1.3, margin: 0, valign: "top", objectName: c.head + " list" });
      x += w + 0.2;
    }
    text(s, "다음", { x: 0.6, y: 5.6, w: 1.0, h: 0.45, fontSize: 18, bold: true, color: C.text2, valign: "middle", objectName: "next head" });
    const next = ["직접 두기 성능 개선", "조사·복기 효과 실측", "리듬 게임·실시간 액션으로 확대"];
    let nx = 1.6;
    for (const n of next) {
      const w = 0.6 + n.length * 0.19;
      chip(s, nx, 5.62, w, n, C.text2, "next " + n);
      nx += w + 0.25;
    }
    s.addNotes([
      "계열 풀이기가 없을 때는 모델이 직접 두는 방식이 주 경로다. 모델이 규칙과 풀이를 코드로 만드는 방식은 스도쿠와 틱택토에서 작동한 사례가 있고, Singles, Flood, Unruly에서는 작동하지 않았다. Flood는 모델이 직접 두는 방식으로 풀었다.",
      "Rogue Adventure 실측에서 저장한 카드 규칙 10개 중 4개를 카드 조합 계산에 활용할 수 있었다. 적이 공격할 때만 방어를 얻는 조건부 효과나 화상처럼 다음 턴까지 이어지는 효과는 조합을 비교할 때 반영하지 못했다(PLAN §36 M11b).",
      "실측은 게임마다 1~3판이다. 판 밖 화면의 예로 Puzzles에서 요청한 게임을 찾지 못한 경우와, 테트리스에서 광고가 결과를 가려 레벨 종료를 놓친 경우가 있다. 구현된 실시간 규칙은 테트리스류의 떨어지는 블록이다.",
    ].join("\n"));
  }

  // 시사점
  {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "마무리" });
    s.addText("ARMY GUI 작업에 대한 시사점", { placeholder: "title" });
    const rows = [
      { icon: "FaCode", color: C.accent2, head: "큰 모델은 판단에만, 반복은 코드로", body: "2048: 코드 풀이기가 플레이 중 모델 개입 없이 2,205수를 진행했다. 모델이 직접 두면 30만 토큰에 80~90수" },
      { icon: "FaRectangleAd", color: C.accent1, head: "어려운 건 규칙보다 게임 밖", body: "광고 · 팝업 · 튜토리얼 · 결과 판정. 테트리스는 광고가 결과를 가려 레벨 끝을 놓쳤다. 일반 앱도 같은 곳에서 막힌다" },
      { icon: "FaBolt", color: C.accent3, head: "모델을 역할별로 나눈다", body: "경량 모델 Jev는 차례가 돌아왔는지 판정하고(약 0.5초), Luna는 코드 판독기가 놓친 칸을 읽는다.\n모델 판단이 필요한 수는 주 모델이 고른다(질문당 약 4.8초)." },
    ];
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i], y = 1.45 + i * 1.4;
      card(s, 0.6, y, 12.13, 1.2, C.background2, r.head);
      await iconCircle(s, r.icon, 0.85, y + 0.25, 0.7, r.color, H.lt1, r.head);
      text(s, r.head, { x: 1.85, y: y + 0.12, w: 10.6, h: 0.45, fontSize: 19, bold: true, color: C.text2, valign: "middle", objectName: r.head + " head" });
      text(s, r.body, { x: 1.85, y: y + 0.58, w: 10.6, h: 0.55, fontSize: 14, color: C.text1, objectName: r.head + " body" });
    }
    text(s, "게임의 점수와 성공·실패 화면으로 GUI 조작 결과를 검증할 수 있다", { x: 0.6, y: 5.75, w: 12.1, h: 0.7, fontSize: 22, bold: true, color: C.text2, objectName: "closing" });
    s.addNotes("게임은 결과가 화면에 드러나므로 GUI 조작의 성공 여부를 검증하기 좋다. 게임마다 다른 조작 방식과 실시간 입력 지연도 확인할 수 있다. 넷째 후보(처음 보는 일은 사람처럼: 찾아보고, 해 보고, 복기한다)는 효과를 아직 재지 못해 뺐다. 넣는다면 '효과는 아직 못 잼'을 함께 말할 것. 화면을 반복해서 확인하는 일에는 경량 모델을 쓴다. Jev는 내 차례가 돌아왔는지 판정하고, Luna(gpt-6-luna)는 코드 판독기가 못 읽은 칸을 분류한다. 주 모델은 다음 수처럼 판단이 필요한 경우에 호출한다. 수치 출처는 STATUS §2·§3과 PLAN §36이다. Jev는 판정당 약 0.45초, 주 모델은 질문당 약 4.8초였다.");
  }

  // 링크
  {
    const s = pres.addSlide({ masterName: "SECTION", sectionTitle: "마무리" });
    s.addText("더 보기", { placeholder: "title" });
    const y = 2.2;
    card(s, 0.6, y, 12.13, 0.68, "26305A", "link");
    s.addImage({ data: await icon("FaLink", H.accent1), x: 0.85, y: y + 0.18, w: 0.32, h: 0.32, objectName: "link icon" });
    text(s, "Confluence", { x: 1.4, y, w: 5.6, h: 0.68, fontSize: 17, bold: true, color: C.background1, valign: "middle", objectName: "link name" });
    text(s, "[링크]", { x: 7.0, y, w: 5.5, h: 0.68, fontSize: 14, color: C.accent5, valign: "middle", italic: true, objectName: "link url" });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 0.6, y, w: 12.13, h: 0.68, rectRadius: 0.12,
      fill: { color: "26305A", transparency: 100 },
      line: { transparency: 100 },
      hyperlink: { url: CONFLUENCE_URL },
      objectName: "Confluence link",
    });
    s.addNotes(CONFLUENCE_URL);
  }

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  // 한글(ea, Hang)도 제목과 본문에 지정한 테마 글꼴을 사용한다.
  {
    const JSZip = require("jszip");
    const zip = await JSZip.loadAsync(fs.readFileSync(OUT), { createFolders: false });
    const p = "ppt/theme/theme1.xml";
    let xml = await zip.file(p).async("string");
    const f = THEME.bodyFontFace;
    xml = xml.replace(/<a:ea typeface=""\/>/g, `<a:ea typeface="${f}"/>`).replace(/<a:font script="Hang" typeface="[^"]*"\/>/g, `<a:font script="Hang" typeface="${f}"/>`);
    zip.file(p, xml);
    fs.writeFileSync(OUT, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
  }
  try { require("child_process").execSync(`zip -dq "${OUT}" '*/'`); } catch (e) { /* 폴더 항목이 없으면 zip이 실패로 끝남 */ }
  console.log("wrote", OUT);
})().catch((e) => { console.error(e); process.exit(1); });
