import copy, json, pathlib
from gamelab.adaptation import Store, Trace, Look
ROOT = pathlib.Path("/Users/jimni/AgenticFW/game-lab")
# name -> (store folder, site folder name, family)
SITES = {
    "ovolve_2048": ("out/web/g5-adapters", "ovolve.github.io-69fb4b03", "slide_merge"),
    "doge2048": ("out/web/step4-adapters", "doge2048.com-3ae67efe", "slide_merge"),
    "play2048_co": ("out/web/step4c-adapters", "play2048.co-170c3ed1", "slide_merge"),
    "mso_mines": ("out/web/g5-adapters", "minesweeperonline.com-cae8d572", "minesweeper"),
    "tatham_mines": ("out/web/g4-adapters", "www.chiark.greenend.org.uk-dfd07662", "minesweeper"),
    "blockblast": ("out/web/g7-adapters", "blockblast.org-c216d3f9", "block_placement"),
    "bazhanius_match3": ("out/web/g8-adapters", "bazhanius.github.io-f6d79036", "match3"),
    "khang_klondike": ("out/web/g9-adapters-2", "khang-nd.github.io-b77ceb20", "klondike"),
}
def load(name):
    """(source, meta, trace) as kept, via Store's own reader."""
    store, folder, family = SITES[name]
    path = ROOT / store / folder / family
    meta = json.loads((path / "adapter.json").read_text())
    looks = [Look((path / f"look-{i}.png").read_bytes(), **{**look, "frames": [
        (path / f"look-{i}-frame-{n}.png").read_bytes() for n in range(look.get("frames", 0))]})
        for i, look in enumerate(meta["trace"])]
    return (path / "adapter.py").read_text(), meta, Trace(family, meta["rules"], meta["site"], looks)
def first(trace, keep=7, held=2):
    """The trace as a game's first generation sees it: the first `keep` looks, the last `held` kept back."""
    looks = [copy.deepcopy(l) for l in trace.looks[:keep]]
    for l in looks: l.held = False
    for l in looks[-held:]: l.held = True
    looks[-1].action = looks[-1].sent = None
    return Trace(trace.family_id, dict(trace.rules), trace.site, looks)
