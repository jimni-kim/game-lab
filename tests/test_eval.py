"""The evaluation commands: what a run writes down and how it is disturbed. Local Chromium, no model."""
import argparse
import json

import pytest

from gamelab import catalog
from gamelab.eval import policies, web
from test_screen import screen_reading  # noqa: F401
from test_web import page, page_game  # noqa: F401

ORACLE = """() => { const grid = Array.from({length: 4}, () => Array(4).fill(0));
  for (const tile of document.querySelectorAll('.tile')) { const value = +/tile-(\\d+)\\b/.exec(tile.className)[1];
    const at = /tile-position-(\\d)-(\\d)/.exec(tile.className); grid[+at[2] - 1][+at[1] - 1] = value; }
  return {grid, score: +document.querySelector('.score-container').textContent}; }"""


def arguments(out, **changed):
    return argparse.Namespace(**{"objective": "actions", "target": 2, "policy": None, "compute": 20, "max_actions": 2,
                                 "max_seconds": 60, "out": out, "perturb": [], "accept_consent": False, "generate": False,
                                 "regenerate": False, "adapter_store": out / "adapters", **changed})


@pytest.mark.parametrize("perturb", [[], ["cancel@1"]], ids=["undisturbed", "cancelled after the first input"])
async def test_a_web_game_is_scored_against_the_pages_answer_key_and_written_down(page, tmp_path, perturb):
    profile, keys, _ = page_game(tmp_path)
    url = (tmp_path / "game.html").as_uri()
    site = {"viewport": [1280, 1000], "oracle": ORACLE, "setup": [{"reload": True}, {"wait_ms": 100}]}
    args = arguments(tmp_path / "out", perturb=[web.perturbation(text) for text in perturb])
    result = await web.play_one(args, 1, page, "local", url, profile, profile.family_id, site, None)
    written = json.loads((tmp_path / "out" / "local-01.json").read_text())
    assert written["result"] == json.loads(json.dumps(result)) and (tmp_path / "out" / "local-01-final.png").stat().st_size
    assert result["resent"] == 0 and result["decisions_misread"] == 0 and result["model_questions"] == 0
    if perturb:
        assert (result["outcome"], result["reason"]) == ("user_stopped", "cancelled")
        assert len(written["inputs"]) == 1 and result["perturbed"][0]["inputs_after"] == 0
    else:
        assert result["outcome"] == "goal_reached" and result["confirmed_actions"] == 2
        assert result["decisions_checked"] == 2 and result["moves"]["effect"]["confirmed"] == 2
        assert [entry["arguments"]["keys"][0] for entry in written["inputs"]] == keys
        assert result["final"]["grid"] != written["inputs"][0]["truth"]["grid"] and result["seconds_per_move"]["mean"] > 0


@pytest.mark.parametrize("text", ["resize@0:10x10", "shake@1", "resize@1", "cover@1:x", "user@2:1,2,3", "cancel@1:later"])
def test_a_misspelt_perturbation_fails_before_any_game(text):
    with pytest.raises(argparse.ArgumentTypeError):
        web.perturbation(text)


def test_the_site_settings_are_ones_a_run_can_start_from():
    """Every evaluation site has a window size; one played without a profile names its page and a known family."""
    sites = json.loads(web.SITES.read_text(encoding="utf-8"))
    for name, site in sites.items():
        if name.startswith("_"):
            continue
        assert len(site["viewport"]) == 2, name
        assert ("family" in site) <= ("url" in site) and site.get("family", "slide_merge") in catalog.MODELS, name


def test_policies_are_compared_on_the_same_seeds_and_a_hidden_game_by_wins():
    game = policies.play("minesweeper", "solver", 20000, 0, 200)
    assert game["score"] is None and game["won"] in (True, False) and game["ended"] == "no_move"
    slide = policies.play("slide_merge", "expectimax", 50, 0, 30)
    assert policies.play("slide_merge", "expectimax", 50, 0, 30)["score"] == slide["score"] and slide["ended"] == "capped"
    assert policies.summarize([game, game])["win_rate"] == float(game["won"])


def test_a_policy_told_nothing_of_a_rule_value_plays_the_same_game_by_the_real_one():
    """`--hide`: the game keeps its mines; only the policy is told no count. It ends when the game does."""
    blind = policies.play("minesweeper", "solver", 20000, 0, 200, ("mines",))
    assert blind["won"] in (True, False) and blind["ended"] == "no_move" and blind["moves"] > 0
