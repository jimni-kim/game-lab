"""The evaluation commands: their arguments, the sites they start from and the policies' own games. No browser,
device or model."""
import argparse
import json

import pytest

from gamelab import catalog
from gamelab.eval import policies, web

@pytest.mark.parametrize("text", ["resize@0:10x10", "shake@1", "resize@1", "cover@1:x", "user@2:1,2,3", "cancel@1:later"])
def test_a_misspelt_perturbation_fails_before_any_game(text):
    with pytest.raises(argparse.ArgumentTypeError):
        web.perturbation(text)


def test_the_site_settings_are_ones_a_run_can_start_from():
    """Every evaluation site has a window size and its page, and a known family unless it is a game of none
    (which the harness finds out and leaves to the model): `--site` alone starts a run."""
    sites = json.loads(web.SITES.read_text(encoding="utf-8"))
    for name, site in sites.items():
        if name.startswith("_"):
            continue
        assert len(site["viewport"]) == 2, name
        assert site["url"].startswith(("https://", "http://")) and site.get("family", "minesweeper") in catalog.MODELS, name


def test_a_web_run_is_named_by_its_site_alone_and_old_commands_still_parse():
    parser = argparse.ArgumentParser()
    web.configure(parser)
    args = parser.parse_args(["--site", "ovolve_2048", "--compute", "20", "--out", "out/x", "--generate"])
    assert args.site == "ovolve_2048" and args.generate and not args.identify
    for removed in (["--profile", "ovolve_2048"], ["--unregistered"], ["--loop", "session"]):
        with pytest.raises(SystemExit):
            parser.parse_args(["--site", "ovolve_2048", "--compute", "20", "--out", "out/x", *removed])
    with pytest.raises(SystemExit):
        parser.parse_args(["--compute", "20", "--out", "out/x"])


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
