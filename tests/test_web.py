"""A game played through real local Chromium on a synthetic page: no external game or paid model is used."""
import json

import pytest

from gamelab import perception, profiles
from gamelab.adapters.reference import ReferenceAdapter
from gamelab.contracts import ActionPlan, Budget, GoalSpec, Observation
from gamelab.environments.web import WebSurface, browser
from gamelab.execution import map_action
from gamelab.play import play
from test_screen import LOAD, SITES, Screen, render, screen_reading  # noqa: F401


@pytest.fixture
async def page():
    async with browser(viewport=[1280, 1000]) as opened:
        yield opened


@pytest.mark.parametrize("profile_id", ["ovolve_2048", "blockblast_org"])
async def test_a_move_through_chromium_is_confirmed_despite_background_motion(page, tmp_path, screen_reading, profile_id):
    """Animated results and transformed pickups must be confirmed despite continuous background motion."""
    game = Screen(profile_id, "blockblast_initial.png" if profile_id == "blockblast_org" else "ovolve_initial.png")
    family, board = game.family, game.board
    # the move the run itself will choose: its default policy at its budget
    action = family.solver.choose(board, family.rules, family.descriptor["default_policy"], 20)["action"]
    (tmp_path / "initial.png").write_bytes(render(board, game.measured))
    (tmp_path / "after.png").write_bytes(render(family.next_state(board, action, 0), game.measured))
    key = game.profile.data["controls"].get("keys", {}).get(action.get("direction"))
    shape = None if key else board["tray"][action["slot"]]
    placed = shape and {"rows": max(r for r, _ in shape) + 1, "cols": max(c for _, c in shape) + 1,
                        "first": shape[0], "target": action["anchor"]}
    viewport = game.measured.layout["viewport"]
    (tmp_path / "game.html").write_text("""<html><body style='margin:0;overflow:hidden'>
<canvas width='1280' height='HEIGHT'></canvas><script>
const canvas=document.querySelector('canvas'), c=canvas.getContext('2d');
const first=new Image(), next=new Image(); window.keys=[];window.ready=false;
first.src='initial.png';next.src='after.png';
Promise.all([first.decode(),next.decode()]).then(()=>{c.drawImage(first,0,0);window.ready=true});
window.drags=[];
const geometry=GEOMETRY;
// Background pixels never settle; only the game regions matter.
setInterval(()=>{c.fillStyle=`rgb(${Math.random()*255},0,0)`;c.fillRect(0,0,20,20)},30);
function finish(){
  const moving=document.createElement('span');moving.textContent='moving';
  document.body.append(moving);
  const animation=setInterval(()=>{moving.style.opacity=String(Math.random())},20);
  setTimeout(()=>{clearInterval(animation);moving.remove();c.drawImage(next,0,0)},600);
}
let start;
document.addEventListener('mousedown',e=>{start=[e.clientX,e.clientY]});
document.addEventListener('mouseup',e=>{
  window.drags.push([...start,e.clientX,e.clientY]);
  if(geometry){
    const [r,c]=geometry.first, [row,col]=geometry.target;
    const firstX=e.clientX-geometry.cols*28+(c+.5)*56;
    const firstY=e.clientY-geometry.rows*.85*56-96+(r+.5)*56;
    if(Math.abs(firstX-(420+(col+c+.5)*56))<3 && Math.abs(firstY-(226+(row+r+.5)*56))<3)finish();
  }
});
document.addEventListener('keydown',e=>{window.keys.push(e.key);if(e.key===KEY)finish()});
</script></body></html>""".replace("GEOMETRY", json.dumps(placed)).replace("KEY", json.dumps(key)).replace("HEIGHT", str(viewport[1])))
    await page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    await page.goto((tmp_path / "game.html").as_uri())
    await page.wait_for_function("window.ready")
    played = await play(WebSurface(page), profile=game.profile, goal=GoalSpec("actions", 1), budget=Budget(1, 20, 60))
    assert played.record["outcome"] == "goal_reached", played.record
    assert played.record["actions_used"] == played.record["confirmed_actions"] == 1
    assert played.moves[0]["delivery"] == "delivered" and played.moves[0]["effect"] == "confirmed"
    if key is not None:
        assert await page.evaluate("window.keys") == [key]
    else:
        seen = Observation("initial", 0, "tab", 1, 0, "clock")
        png = (SITES / "blockblast_initial.png").read_bytes()
        state = perception.estimate(seen, png, game.measured)
        mapped = map_action(ActionPlan("plan", state.id, state.rule_revision, "test", (action,), 1),
                            state, seen, png, ReferenceAdapter.measured_as(game.measured))
        expected = [int(mapped.arguments[k]) for k in ("start_x", "start_y", "end_x", "end_y")]
        assert [[int(value) for value in drag] for drag in await page.evaluate("window.drags")] == [expected]


async def test_a_page_reading_game_reads_its_elements_and_pictures_only_its_board(page, tmp_path):
    """A page-reading game reads its elements and pictures only its board, and a move's confirmed result is
    the next move's only look."""
    game = Screen()                      # measured as the screen reads it; the run plays the page-reading profile
    profile, family = LOAD("ovolve_2048"), game.family
    assert "reading" in profile.data
    states, keys = [{"grid": [[2, 0, 0, 0], [0, 0, 0, 0], [0, 0, 2, 0], [0, 0, 0, 4]], "score": 0, "scene": "playing"}], []
    for _ in range(2):
        action = family.solver.choose(states[-1], family.rules, family.descriptor["default_policy"], 20)["action"]
        keys.append(profile.data["controls"]["keys"][action["direction"]])
        states.append(family.next_state(states[-1], action, 0))
    left, top, width, height = game.measured.layout["board"]["rect"]
    colours = {entry["value"]: "rgb({},{},{})".format(*entry["rgb"]) for entry in profile.data["board"]["palette"]}
    # the board's frame around it (its border a pixel wider at the left and bottom) and the score box its
    # anchors sit on, as the site draws them
    frame = "rgb({},{},{})".format(*profile.data["board"]["frame"]["rgb"])
    pages = [{"score": state["score"], "tiles": "".join(
        f"<div class='tile tile-{value} tile-position-{col + 1}-{row + 1}' style='position:absolute;"
        f"left:{col * width / 4}px;top:{row * height / 4}px;width:{width / 4}px;height:{height / 4}px;"
        f"background:{colours[value]}'></div>" for row, cells in enumerate(state["grid"])
        for col, value in enumerate(cells) if value)} for state in states]
    (tmp_path / "game.html").write_text(f"""<html><body style='margin:0;overflow:hidden;width:1280px;height:1000px'>
<div class='score-container' style='position:absolute;left:870px;top:80px;width:30px;height:70px;background:{frame};color:{frame}'>0</div>
<div style='position:absolute;left:{left - 8}px;top:{top - 7}px;width:{width + 15}px;height:{height + 15}px;
 background:{frame}'></div>
<div class='game-container' style='position:absolute;left:{left}px;top:{top}px;width:{width}px;height:{height}px;
 background:{colours[0]}'><div class='game-message'></div><div class='tile-container'></div></div><script>
const pages={json.dumps(pages)}, keys={json.dumps(keys)}; let at=0; window.keys=[];
const show=page=>{{document.querySelector('.tile-container').innerHTML=page.tiles;
  document.querySelector('.score-container').textContent=String(page.score)}};
show(pages[0]);
// the page changes a move's tiles a little after the key, as the game's own rendering does
document.addEventListener('keydown',e=>{{window.keys.push(e.key);
  if(e.key===keys[at]){{at+=1;setTimeout(()=>show(pages[at]),100)}}}});
</script></body></html>""")
    await page.goto((tmp_path / "game.html").as_uri())
    surface = WebSurface(page)
    shots, capture = [], surface.capture

    async def counted(region=None):
        png = await capture(region)
        shots.append((region, png[16:24]))
        return png
    surface.capture = counted
    played = await play(surface, profile=profile, goal=GoalSpec("actions", 2), budget=Budget(2, 20, 60))
    assert played.record["outcome"] == "goal_reached" and played.record["confirmed_actions"] == 2, played.record
    assert await page.evaluate("window.keys") == keys
    # the board was measured on the first look, the whole screen; every later look pictures it alone
    assert shots[0][0] is None and all(region == [left, top, left + width, top + height] for region, _ in shots[1:])
    assert all(size == width.to_bytes(4, "big") + height.to_bytes(4, "big") for _, size in shots[1:])
    assert played.geometry["board"] == [left, top, width, height]


async def test_elements_are_read_by_selector_with_their_own_text_and_never_a_field(page):
    await page.set_content("<div class='score a b'>12<span>+4</span></div><input class='score' value='secret'>"
                           "<p class='tile'>x</p><p class='tile'>y</p>")
    found = await WebSurface(page).elements([{"name": "score", "selector": ".score", "max": 2},
                                             {"name": "tiles", "selector": ".tile", "max": 1},
                                             {"name": "none", "selector": ".absent", "max": 1}])
    assert found["score"] == {"count": 2, "truncated": False,
                              "items": [{"classes": ["score", "a", "b"], "text": "12"}, {"protected": True}]}
    assert found["tiles"] == {"count": 2, "truncated": True, "items": [{"classes": ["tile"], "text": "x"}]}
    assert found["none"] == {"count": 0, "truncated": False, "items": []}
    assert await WebSurface(page).elements([{"name": "bad", "selector": "::", "max": 1}]) is None
