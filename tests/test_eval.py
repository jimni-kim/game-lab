"""The evaluation commands: their arguments, the sites they start from and the policies' own games. No browser,
device or model."""
import argparse
import asyncio
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
    # a run names its page by --site, or by the address in a request in words (--ask)
    asked = parser.parse_args(["--ask", "play 2048 at https://x.example/ to 512", "--compute", "20", "--out", "out/x"])
    assert asked.site is None and asked.ask.startswith("play 2048")
    with pytest.raises(SystemExit):
        asyncio.run(web.main(parser.parse_args(["--compute", "20", "--out", "out/x"])))


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


def test_a_page_played_on_from_the_screen_is_opened_with_no_setup_and_keeps_what_it_saved(tmp_path):
    """PLAN §25, R4 on the web: a page a request named keeps what it saves in its host's browser profile, as an
    app keeps its data; work that goes on from the screen opens it with no reload and none of a site's setup."""
    import argparse

    from gamelab.contracts import GoalSpec, Scope
    from gamelab.eval import web

    parser = argparse.ArgumentParser()
    web.configure(parser)
    args = parser.parse_args(["--compute", "1", "--out", str(tmp_path)])
    asked = {"url": "https://Games.example:8443/match", "goal": GoalSpec("levels", 3, scope=Scope.NEW_GAME), "hint": None}
    _, site = web.site_for(args, asked)
    assert {"reload": True} in site["setup"] and web.profile_for(args, site) == web.PROFILES / "games.example"
    _, site = web.site_for(args, {**asked, "goal": GoalSpec("levels", 3, scope=Scope.CURRENT_GAME)})
    assert site["setup"] == [{"wait_ms": web.ASKED_WAIT_MS}]
    # a site of sites.json starts with nothing saved, unless the run goes on from where the last one stopped
    args = parser.parse_args(["--compute", "1", "--out", str(tmp_path), "--site", "ovolve_2048"])
    _, site = web.site_for(args, None)
    assert site["setup"] and web.profile_for(args, site) is None
    args = parser.parse_args(["--compute", "1", "--out", str(tmp_path), "--site", "ovolve_2048", "--resume"])
    _, site = web.site_for(args, None)
    assert site["setup"] == [] and web.profile_for(args, site) == web.PROFILES / "ovolve.github.io"


def test_a_rule_value_given_to_the_policies_run_is_the_game_played_and_the_policy_named_is_the_one_run():
    """PLAN §33 P1: `--rule` binds the families that have the rule (a larger grid, the first cell's promise),
    `--policy` plays any policy the family offers."""
    parser = argparse.ArgumentParser()
    policies.configure(parser)
    args = parser.parse_args(["--rule", "rows=16", "--rule", "mines=40", "--rule", "first_open=cell", "--rule", "draw=3"])
    values = dict(args.rule)
    assert values == {"rows": 16, "mines": 40, "first_open": "cell", "draw": 3}
    assert policies.bound("minesweeper", values).rules == {"rows": 16, "cols": 9, "mines": 40, "flags": False, "first_open": "cell"}
    assert policies.bound("klondike", values).rules == {"draw": 3} and policies.bound("slide_merge", values).rules["size"] == 4
    with pytest.raises(SystemExit):
        policies.bound("minesweeper", {"rows": 1})
    game = policies.play("minesweeper", "solver", 20000, 0, 400, (), {"rows": 12, "cols": 12, "mines": 20, "first_open": "cell"})
    assert game["won"] in (True, False) and game["ended"] == "no_move"
    # a deal greedy cannot win ends with no move left, not at the cap of moves
    deals = [policies.play("klondike", "greedy", 30, seed, 3000) for seed in range(6)]
    assert all(deal["ended"] == "no_move" or deal["won"] for deal in deals) and not all(deal["won"] for deal in deals)
