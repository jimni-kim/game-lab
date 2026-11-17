import pytest


@pytest.fixture(autouse=True)
def _instant_looks(monkeypatch):
    """A fake screen is drawn the moment it is captured: the second look and the frames that leave out what keeps
    moving (`motion`) need no time between them here, nor the frames a carried drag looks at."""
    from gamelab import carry, motion

    monkeypatch.setattr(motion, "GAP_S", 0.0)
    monkeypatch.setattr(motion, "FRAME_GAP_S", 0.0)
    monkeypatch.setattr(carry, "STILL_GAP_S", 0.0)


@pytest.fixture(autouse=True)
def _readers_under_test(request, monkeypatch):
    """The tests written for a game read by code a model writes (2048, minesweeper and match-3 on fake screens)
    keep that way of reading: only the tests of the families' own readers (`test_kit*.py`) see every one of
    them. A block game is read by its family's reader everywhere."""
    from gamelab import kits

    if not request.module.__name__.startswith("test_kit"):
        monkeypatch.setattr(kits, "KITS", {"block_placement": kits.KITS["block_placement"]})


@pytest.fixture(autouse=True)
def _profiles_kept_aside(tmp_path, monkeypatch):
    """What pages save is kept in a browser profile of their host (`eval web`): under each test's own folder here,
    never the repository's `out/`."""
    from gamelab.eval import web

    monkeypatch.setattr(web, "PROFILES", tmp_path / "browser")
