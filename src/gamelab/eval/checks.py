"""What a block game's cells and pieces are, told three ways on screens kept from earlier games (PLAN §19, K0).

No device and no page: the screens a model read in earlier games (an adapter store's looks, with the state the model
read on each and the rules bore out) are the answers. Each way is asked about every screen given:

    host      no model: the empty cells of a source's first screen are learned (each cut to its inner part and made
              8 by 8 pixels) and every cell after is a block when it looks like none of them. The prototype of the
              family reader's cell test
    cells     a light model (`--checker`) asked once a screen about every cell cut out of it: is a block there
    tray      the same model asked about each piece waiting in the tray, cut out of the screen: which of the pieces
              the screens showed is it

  .venv/bin/python -m gamelab.eval checks --store out/adapters/SITE/block_placement/unadopted \\
      --checker jev --checker gpt-6-luna --out out/checks/k0

`jev` is TypeSafe's decisions model on OpenRouter (AGENTIC_OPENROUTER_KEY); any other name is a chat model of the
proxy (AGENTIC_OPENAI_KEY). Results: `checks.json` under `--out`, and a table per way.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import httpx

from .. import checker, model
from . import measure

INSET, SIDE = 0.2, 8
# a cell's cut for a model: the cell and this share of it around it
CELL_MARGIN, PIECE_MARGIN = 0.0, 0.25
DECISIVE = 0.2
AT_ONCE = 3
CONTEXT = "Pictures cut out of a screenshot of a block puzzle game: a grid board on which pieces made of square blocks are placed."
CELL_QUESTION = ("{image} is one cell of the game's board, cut out and enlarged. Is a block placed in this cell (a filled "
                 "square tile of any colour or pattern), rather than an empty slot of the board? A glow, text, sparkle or "
                 "other effect passing over an empty slot is not a block.")
PIECE_QUESTION = ("{image} is one piece waiting beside the board to be placed, cut out and enlarged. Which shape is it? Each "
                  "option draws the shape row by row from the top, '#' a block and '.' no block, rows separated by '/'.")


def looks_of(folder: Path) -> list[dict[str, Any]]:
    """The screens of one folder of an adapter store, with what was read on each."""
    if (folder / "trace.json").is_file():
        trace = json.loads((folder / "trace.json").read_text())["trace"]
    else:
        trace = json.loads((folder / "adapter.json").read_text())["trace"]
    return [{"source": str(folder), "index": index, "png": (folder / f"look-{index}.png").read_bytes(), **look}
            for index, look in enumerate(trace) if (folder / f"look-{index}.png").is_file()]


def _image(png: bytes) -> Any:
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return image.convert("RGB")


def _png(image: Any) -> bytes:
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def cell_boxes(look: dict[str, Any]) -> list[tuple[tuple[int, int], list[float]]]:
    left, top, width, height = look["board"]
    board = look["state"]["board"]
    rows, cols = len(board), len(board[0])
    return [((r, c), [left + c * width / cols, top + r * height / rows, width / cols, height / rows])
            for r in range(rows) for c in range(cols)]


def _cut(image: Any, box: list[float], margin: float) -> Any:
    x, y, w, h = box
    return image.crop((round(x - w * margin), round(y - h * margin), round(x + w * (1 + margin)), round(y + h * (1 + margin))))


def _look_of_cell(image: Any, box: list[float]) -> list[int]:
    x, y, w, h = box
    inner = image.crop((round(x + w * INSET), round(y + h * INSET), round(x + w * (1 - INSET)), round(y + h * (1 - INSET))))
    return list(inner.resize((SIDE, SIDE)).tobytes())


def _distance(one: list[int], other: list[int]) -> float:
    return sum(abs(a - b) for a, b in zip(one, other)) / len(one)


def host(looks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each cell told by how unlike it is to the empty cells of its source's first screen."""
    rows = []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for look in looks:
        by_source.setdefault(look["source"], []).append(look)
    for source, kept in by_source.items():
        first = kept[0]
        image = _image(first["png"])
        seen = [(value, _look_of_cell(image, box)) for ((r, c), box), value in
                zip(cell_boxes(first), (v for row in first["state"]["board"] for v in row))]
        empties = [cell for value, cell in seen if value == 0]
        apart = [min(_distance(cell, other) for other in empties if other is not cell) for value, cell in seen if value == 0] \
            if len(empties) > 1 else [0.0]
        blocks = [min(_distance(cell, other) for other in empties) for value, cell in seen if value == 1]
        cut = (max(apart) + min(blocks)) / 2 if blocks else max(3 * max(apart), 20.0)
        for look in kept:
            image = _image(look["png"])
            board = look["state"]["board"]
            for (r, c), box in cell_boxes(look):
                if board[r][c] is None:
                    continue
                far = min(_distance(_look_of_cell(image, box), other) for other in empties)
                rows.append({"source": source, "index": look["index"], "cell": [r, c], "truth": board[r][c],
                             "said": int(far > cut), "far": round(far, 1), "cut": round(cut, 1)})
    return rows


def _shape(piece: list[list[int]]) -> str:
    rows, cols = max(r for r, _ in piece) + 1, max(c for _, c in piece) + 1
    cells = {tuple(cell) for cell in piece}
    return "/".join("".join("#" if (r, c) in cells else "." for c in range(cols)) for r in range(rows))


async def ask_cells(check: Any, look: dict[str, Any]) -> list[dict[str, Any]]:
    image = _image(look["png"])
    board = look["state"]["board"]
    checks, truths = [], {}
    for (r, c), box in cell_boxes(look):
        if board[r][c] is None:
            continue
        key = f"c{r}_{c}"
        checks.append(checker.Check(key, checker.NOUL, CELL_QUESTION, _png(_cut(image, box, CELL_MARGIN))))
        truths[key] = ((r, c), board[r][c])
    try:
        answers = await check.ask(checks, CONTEXT)
    except Exception as error:
        return [{"source": look["source"], "index": look["index"], "error": f"{type(error).__name__}: {error}"[:300]}]
    return [{"source": look["source"], "index": look["index"], "cell": list(cell), "truth": truth,
             "p": answers.get(key, {}).get("p")} for key, (cell, truth) in truths.items()]


async def ask_tray(check: Any, look: dict[str, Any], options: dict[str, str]) -> list[dict[str, Any]]:
    image = _image(look["png"])
    sources = look.get("sources") or []
    checks, truths = [], {}
    for slot, piece in enumerate(look["state"].get("tray") or []):
        box = sources[slot] if slot < len(sources) else None
        if not piece or not box:
            continue
        key = f"t{slot}"
        checks.append(checker.Check(key, checker.CHOICE, PIECE_QUESTION, _png(_cut(image, box, PIECE_MARGIN)),
                                    options={name: None for name in options} if isinstance(check, checker.Jev) else options))
        truths[key] = _shape(piece)
    if not checks:
        return []
    if isinstance(check, checker.Jev):
        # Jev's options are the shapes themselves (their drawings as the keys say nothing)
        checks = [checker.Check(c.key, c.kind, c.instructions, c.image, options=dict(options)) for c in checks]
    try:
        answers = await check.ask(checks, CONTEXT)
    except Exception as error:
        return [{"source": look["source"], "index": look["index"], "error": f"{type(error).__name__}: {error}"[:300]}]
    return [{"source": look["source"], "index": look["index"], "slot": key, "truth": truth,
             "said": options.get(answers.get(key, {}).get("choice") or "", None), "p": answers.get(key, {}).get("p")}
            for key, truth in truths.items()]


def cell_table(rows: list[dict[str, Any]], *, wrong_for_host: set[tuple] | None = None) -> dict[str, Any]:
    told = [row for row in rows if "truth" in row]
    answered = [row for row in told if row.get("p") is not None or "said" in row]
    said = [(row, row["said"] if "said" in row else int(row["p"] >= 0.5)) for row in answered]
    decisive = [(row, guess) for row, guess in said if "said" in row or row["p"] >= 1 - DECISIVE or row["p"] <= DECISIVE]
    out = {"cells": len(told), "answered": len(answered), "right": sum(guess == row["truth"] for row, guess in said),
           "missed_blocks": sum(guess == 0 and row["truth"] == 1 for row, guess in said),
           "false_blocks": sum(guess == 1 and row["truth"] == 0 for row, guess in said),
           "decisive": len(decisive), "decisive_right": sum(guess == row["truth"] for row, guess in decisive),
           "errors": sum("error" in row for row in rows)}
    if wrong_for_host is not None:
        hard = [(row, guess) for row, guess in said if (row["source"], row["index"], tuple(row["cell"])) in wrong_for_host]
        out["where_host_was_wrong"] = {"cells": len(hard), "right": sum(guess == row["truth"] for row, guess in hard)}
    return out


async def prices(base: str, key: str) -> dict[str, tuple[float, float]]:
    """Dollars a token in and out of each proxy model, as the proxy says."""
    root = base.rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}) as http:
            response = await http.get(f"{root}/model/info", timeout=30)
            response.raise_for_status()
    except Exception:
        return {}
    out = {}
    for entry in response.json().get("data", []):
        info = entry.get("model_info") or {}
        if info.get("input_cost_per_token") is not None:
            out[entry["model_name"]] = (info["input_cost_per_token"], info.get("output_cost_per_token") or 0.0)
    return out


async def main(args: argparse.Namespace) -> int:
    looks, seen = [], set()
    for folder in args.store:
        for look in looks_of(folder):
            digest = hashlib.sha256(look["png"]).hexdigest()
            if digest in seen or not isinstance(look.get("state"), dict) or "board" not in look["state"]:
                continue
            seen.add(digest)
            looks.append(look)
    if args.per_source:
        kept: dict[str, int] = {}
        chosen = []
        for look in looks:
            if kept.get(look["source"], 0) < args.per_source:
                kept[look["source"]] = kept.get(look["source"], 0) + 1
                chosen.append(look)
        looks = chosen
    if not looks:
        raise SystemExit("no screens: name folders of an adapter store with --store")
    report: dict[str, Any] = {"revision": measure.revision(), "stores": [str(folder) for folder in args.store],
                              "screens": len(looks), "ways": {}}
    host_rows = host(looks)
    wrong = {(row["source"], row["index"], tuple(row["cell"])) for row in host_rows if row["said"] != row["truth"]}
    report["ways"]["host"] = {"cells": cell_table(host_rows), "wrong": [row for row in host_rows if row["said"] != row["truth"]]}
    print(f"{len(looks)} screens from {len(args.store)} folders")
    print("host", json.dumps(report["ways"]["host"]["cells"]))
    shapes = sorted({_shape(piece) for look in looks for piece in look["state"].get("tray") or [] if piece})
    options = {f"s{index}": shape for index, shape in enumerate(shapes)}
    key, base = model.api_key(), args.model_base
    priced = await prices(base, key) if key else {}
    for name in args.checker or []:
        check = checker.Jev() if name == "jev" else checker.Chat(name, key or "", base=base, prices=priced.get(name))
        bound = asyncio.Semaphore(AT_ONCE)

        async def bounded(job: Any) -> Any:
            async with bound:
                return await job
        try:
            cells = [row for rows in await asyncio.gather(*(bounded(ask_cells(check, look)) for look in looks)) for row in rows]
            tray = [] if args.no_tray else [row for rows in await asyncio.gather(
                *(bounded(ask_tray(check, look, options)) for look in looks)) for row in rows]
        finally:
            await check.close()
        told_tray = [row for row in tray if "truth" in row]
        result = {"cells": cell_table(cells, wrong_for_host=wrong),
                  "tray": {"pieces": len(told_tray), "right": sum(row["said"] == row["truth"] for row in told_tray),
                           "errors": sum("error" in row for row in tray), "options": len(options)},
                  "spent": check.spent.summary(), "per_screen_s": round(check.spent.seconds / max(1, check.spent.asked), 2),
                  "rows": {"cells": cells, "tray": tray}}
        report["ways"][name] = result
        print(name, json.dumps({k: v for k, v in result.items() if k != "rows"}))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "checks.json").write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    print(args.out / "checks.json")
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--store", type=Path, action="append", required=True,
                        help="a folder of an adapter store holding looks (adapter.json or trace.json beside look-N.png); repeatable")
    parser.add_argument("--checker", action="append", help="a light model to ask: jev, or a proxy model name (repeatable)")
    parser.add_argument("--per-source", type=int, help="at most this many screens from each folder")
    parser.add_argument("--no-tray", action="store_true", help="ask about the cells only")
    parser.add_argument("--model-base", default=model.api_base(), help="the proxy's base URL")
    parser.add_argument("--out", type=Path, required=True)
