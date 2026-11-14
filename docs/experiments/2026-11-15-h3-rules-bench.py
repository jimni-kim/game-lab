"""H3: can a model write a game's rules and solver as code from a description and a few observed moves?
No site: kept looks give the observed moves, the shared fixtures and the hand-written rule models score the result."""
import asyncio, copy, json, random, sys, time, pathlib
from common import *
from gamelab import catalog, model
from gamelab.adaptation import _board, _source
from gamelab.model import Msg, TurnRequest
from gamelab.sandbox import Sandbox, SandboxError

PROMPT = (pathlib.Path(__file__).parent / "rules_prompt.md").read_text()
FIXTURES = ROOT / "tests/fixtures/game"
TRACES = {"slide_merge": "ovolve_2048", "minesweeper": "mso_mines", "match3": "bazhanius_match3",
          "block_placement": "blockblast", "klondike": "khang_klondike"}
ATTEMPTS, SHOWN, GAMES = 3, 6, 20
CAPS = {"slide_merge": 300, "minesweeper": 200, "match3": 100, "block_placement": 100, "klondike": 300}

def transitions(trace):
    out = []
    for a, b in zip(trace.looks, trace.looks[1:]):
        if a.action is not None:
            out.append({"state": a.state, "action": a.action, "after": b.state})
    return out

class Code:
    def __init__(self, source): self.box, self.errors = Sandbox(source, call_timeout_s=5.0), 0
    def call(self, name, **kw):
        try: return True, self.box.call(name, **kw)
        except SandboxError as e:
            self.errors += 1
            return False, f"{e.reason}: {e.detail}"[:300]
    def close(self): self.box.close()

def canon(x): return json.dumps(x, sort_keys=True)

def check_examples(code, examples, rules, limit=4):
    """What the model is told: its module against the moves really played."""
    problems = []
    for i, ex in enumerate(examples):
        ok, legal = code.call("legal_actions", state=ex["state"], rules=rules)
        if not ok: problems.append(f"legal_actions on example {i}: {legal}")
        elif not isinstance(legal, list) or canon(ex["action"]) not in {canon(a) for a in legal}:
            problems.append(f"legal_actions on example {i} does not offer the move that was played, {json.dumps(ex['action'])}")
        ok, said = code.call("assess", before=ex["state"], action=ex["action"], after=ex["after"], rules=rules)
        if not ok: problems.append(f"assess on example {i}: {said}")
        elif said is not True: problems.append(f"assess on example {i} refuses the result that was really seen")
        ok, said = code.call("assess", before=ex["state"], action=ex["action"], after=ex["state"], rules=rules)
        if ok and said is not False: problems.append(f"assess on example {i} takes the unchanged screen as the move's result")
        ok, pick = code.call("choose", state=ex["state"], rules=rules)
        if not ok: problems.append(f"choose on example {i}: {pick}")
        elif ok and isinstance(legal, list) and canon(pick) not in {canon(a) for a in legal}:
            problems.append(f"choose on example {i} gave {json.dumps(pick)[:120]}, which legal_actions does not offer")
        ok, st = code.call("status", state=ex["state"], rules=rules)
        if not ok: problems.append(f"status on example {i}: {st}")
        elif st != "playing": problems.append(f"status on example {i} says {st!r} of a game that was played on")
        if len(problems) >= limit: break
    return problems[:limit]

def score_fixtures(code, family_id, meta):
    out = {}
    path = FIXTURES / family_id / "legal_actions.json"
    cases = [c for c in json.loads(path.read_text())["cases"] if "expected" in c and c["input"]["state"].get("scene", "playing") == "playing"]
    right = 0
    for c in cases:
        want = {canon(a) for a in c["expected"] if a["kind"] not in meta}
        ok, got = code.call("legal_actions", state=c["input"]["state"], rules=c["input"]["rules"])
        right += ok and isinstance(got, list) and {canon(a) for a in got} == want
    out["legal_fixture"] = f"{right}/{len(cases)}"
    path = FIXTURES / family_id / "assess.json"
    cases = [c for c in json.loads(path.read_text())["cases"] if "expected" in c and c["expected"].get("effect") in ("confirmed", "absent", "inconsistent")
             and c["input"]["action"]["kind"] not in meta]
    right, wrong = 0, []
    for c in cases:
        i = c["input"]
        ok, got = code.call("assess", before=i["before"], action=i["action"], after=i["after"], rules=i["rules"])
        hit = ok and got is (c["expected"]["effect"] == "confirmed")
        right += hit
        if not hit: wrong.append(c["name"][:60])
    out["assess_fixture"] = f"{right}/{len(cases)}"; out["assess_wrong"] = wrong[:4]
    return out

def score_held(code, held, rules):
    legal = taken = 0
    for ex in held:
        ok, got = code.call("legal_actions", state=ex["state"], rules=rules)
        legal += ok and isinstance(got, list) and canon(ex["action"]) in {canon(a) for a in got}
        ok, said = code.call("assess", before=ex["state"], action=ex["action"], after=ex["after"], rules=rules)
        taken += ok and said is True
    return {"held": len(held), "held_legal": legal, "held_assess": taken}

def play(family, chooser, seed, cap):
    """One simulated game by the hand-written rules, the moves chosen by `chooser(shown state)`."""
    rng = random.Random(seed)
    state = family.initial_state(rng.getrandbits(64))
    moves, ended = 0, "capped"
    while moves < cap:
        shown = family.shown(copy.deepcopy(state))
        legal = [a for a in family.model.legal_actions(shown, family.rules) if a["kind"] not in family.meta_actions]
        if not legal: ended = "no_move"; break
        action = chooser(shown)
        if action is None: ended = "passed"; break
        if canon(action) not in {canon(a) for a in legal}: ended = "illegal"; break
        state = family.next_state(state, action, rng.getrandbits(64)); moves += 1
    shown = family.shown(copy.deepcopy(state))
    won = family.model.status(shown, family.rules, {"objective": "play_until_end"})["goal"] == "reached"
    grid = state.get("grid")
    return {"moves": moves, "ended": ended, "won": won, "score": state.get("score"),
            "max_tile": max(max(r) for r in grid) if family.id == "slide_merge" else None}

def summary(games):
    n = len(games)
    out = {"won": sum(g["won"] for g in games), "ended": {k: sum(g["ended"] == k for g in games) for k in sorted({g["ended"] for g in games})},
           "moves": round(sum(g["moves"] for g in games) / n, 1)}
    if all(g["score"] is not None for g in games): out["score"] = round(sum(g["score"] for g in games) / n, 1)
    if games[0]["max_tile"]: out["max_tile_median"] = sorted(g["max_tile"] for g in games)[n // 2]
    return out

async def one(family_id, described, client, out):
    _, _, trace = load(TRACES[family_id])
    descriptor = catalog.descriptor(family_id)
    family = catalog.family(family_id)
    meta = {m["id"] for m in descriptor.get("meta_actions", [])}
    moves = transitions(trace)
    shown, held = moves[:SHOWN], moves[SHOWN:]
    game = {"state_fields": descriptor["state_fields"], "actions": descriptor["actions"], "scenes": descriptor["scenes"],
            **({"summary": descriptor["summary"]} if described else {})}
    tag = f"{family_id}-{'described' if described else 'observed'}"
    folder = out / tag; folder.mkdir(parents=True, exist_ok=True)
    result = {"family": family_id, "described": described, "attempts": []}
    previous, code, started = None, None, time.monotonic()
    for attempt in range(1, ATTEMPTS + 1):
        context = {"game": game, "rules": trace.rules, "examples": shown, **({"previous": previous} if previous else {})}
        try:
            answer = await client.complete(TurnRequest(system_stable=PROMPT, max_tokens=16000, timeout_s=300.0,
                                                       messages=[Msg("user", [{"type": "text", "text": json.dumps(context)}])]))
        except Exception as e:
            result["attempts"].append({"error": f"{type(e).__name__}: {e}"[:200]}); continue
        source = _source(answer.text or "")
        (folder / f"attempt-{attempt}.py").write_text(source)
        if code: code.close()
        code = Code(source)
        problems = await asyncio.to_thread(check_examples, code, shown, trace.rules)
        result["attempts"].append({"problems": problems, "lines": len(source.splitlines())})
        if not problems: break
        previous = {"module": source, "problems": problems}
    result["accepted"] = bool(result["attempts"]) and result["attempts"][-1].get("problems") == []
    result["write_seconds"] = round(time.monotonic() - started)
    if code is not None:
        def rest():
            scored = {**score_fixtures(code, family_id, meta), **score_held(code, held, trace.rules)}
            cap = CAPS[family_id]
            def generated(state):
                ok, pick = code.call("choose", state=state, rules=family.rules)
                return pick if ok else None
            t = time.monotonic()
            mine = [play(family, generated, seed, cap) for seed in range(GAMES)]
            scored["play_generated"] = summary(mine); scored["choose_ms"] = round((time.monotonic() - t) * 1000 / max(1, sum(g["moves"] for g in mine)))
            for name in ("default_policy", "baseline_policy"):
                policy = descriptor[name]
                scored[f"play_{name.split('_')[0]}"] = summary([play(family, lambda s: family.choose(s, policy, descriptor["default_compute"]), seed, cap) for seed in range(GAMES)])
            scored["sandbox_errors"] = code.errors
            return scored
        result.update(await asyncio.to_thread(rest))
        code.close()
    (folder / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result)[:1500], flush=True)
    return result

async def main(model_name, families):
    client = model.Recorded(model.ChatClient(model_name, model.api_key(), base=model.api_base()))
    out = ROOT / "out/review/h3" / model_name
    results = await asyncio.gather(*(one(f, d, client, out) for f in families for d in (True, False)))
    (out / "all.json").write_text(json.dumps({"results": results, "tokens": client.tokens()}, indent=1))
    print("TOKENS", client.tokens())
asyncio.run(main(sys.argv[1], sys.argv[2:] or list(TRACES)))
