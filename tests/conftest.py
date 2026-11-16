import pytest


@pytest.fixture(autouse=True)
def _instant_looks(monkeypatch):
    """A fake screen is drawn the moment it is captured: the second look and the frames that leave out what keeps
    moving (`motion`) need no time between them here."""
    from gamelab import motion

    monkeypatch.setattr(motion, "GAP_S", 0.0)
    monkeypatch.setattr(motion, "FRAME_GAP_S", 0.0)
