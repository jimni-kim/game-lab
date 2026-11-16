"""Frames with the time their pixels were taken (PLAN §18): a polled surface waits only for a time still to come,
frames keep their gaps from when each was taken, and a surface that gives frames of its own is looked at only
through them."""
import time

from gamelab import carry, frames, motion
from gamelab.carry import Carrier
from gamelab.frames import Frame, Polled
from gamelab.motion import Steady
from test_adaptation_blocks import Blocks
from test_motion import Following, Glinting, goal_at, slot_middle


class Slow:
    """A surface whose capture takes `took` seconds."""

    def __init__(self, took):
        self.took, self.captures = took, 0

    async def capture(self, region=None):
        self.captures += 1
        time.sleep(self.took)
        return b"png%d" % self.captures


async def test_a_polled_frame_is_captured_at_once_and_one_for_a_time_to_come_is_waited_for():
    source = Polled(Slow(0.02))
    began = time.monotonic()
    now = await source.frame()
    assert now.at >= began and now.took >= 0.02
    # a time already gone by is no wait
    again = await source.frame(now.at)
    assert again.at - now.at < 0.05 and source.waited == 0.0
    later = await source.frame(time.monotonic() + 0.1)
    assert later.at - again.at >= 0.1 and source.waited > 0.05
    told = source.summary()
    assert told["frames"] == 3 and told["source"] == "polled" and told["per_frame_s"] >= 0.02


async def test_frames_keep_their_gap_from_when_each_was_taken_not_from_when_it_came():
    # each capture takes longer than the gap: no time is waited between them
    slow = Polled(Slow(0.05))
    got = await frames.frames(slow, 3, 0.03)
    assert len(got) == 3 and slow.waited == 0.0
    assert all(b.at - a.at >= 0.03 for a, b in zip(got, got[1:]))
    # captures quicker than the gap: each is waited for
    quick = Polled(Slow(0.0))
    got = await frames.frames(quick, 3, 0.04)
    assert all(b.at - a.at >= 0.04 for a, b in zip(got, got[1:]))


class Fed:
    """A frame source a surface gives of its own (frames as they are drawn), counting what it is asked for."""

    def __init__(self, surface):
        self.surface, self.asked = surface, []

    async def frame(self, after=None):
        self.asked.append(after)
        return Frame(await self.surface.capture(), max(after or 0.0, time.monotonic()))

    def summary(self):
        return {"source": "fed", "frames": len(self.asked)}


class Offering(Glinting):
    def __init__(self):
        super().__init__()
        self.frame_source = Fed(self)


async def test_a_surface_that_gives_frames_of_its_own_is_looked_at_through_them():
    fake = Offering()
    seen = Steady(fake)
    assert seen.frame_source is fake.frame_source
    await seen.capture()
    # the glint was drawn through: every frame came from the surface's own source
    assert seen.animating and len(fake.frame_source.asked) == motion.FRAMES == fake.captures
    # and so do the looks a carried drag takes
    assert Carrier(seen).frames is fake.frame_source


async def test_a_carry_compares_frames_taken_at_least_the_still_gap_apart(monkeypatch):
    monkeypatch.setattr(carry, "STILL_GAP_S", 0.03)
    fake = Following(gain=1.45, catch=0.5)
    taken = []
    source = Polled(fake)
    frame = source.frame

    async def kept(after=None):
        got = await frame(after)
        taken.append((after, got.at))
        return got
    source.frame = kept
    told = await Carrier(fake, source).carry(slot_middle(fake, 0), goal_at(fake, 0, [2, 2]), 400)
    assert fake.placed == [(0, [2, 2])], told
    # within each look the frames are the gap apart: every frame after the first of a look was asked for a time
    # STILL_GAP_S after the one before it, and taken no earlier
    assert all(at >= after for after, at in taken if after is not None)
    within = [(one, other) for one, other in zip(taken, taken[1:])
              if other[0] is not None and abs(other[0] - (one[1] + 0.03)) < 1e-9]
    assert len(within) >= len(told["looks"]) and all(other[1] - one[1] >= 0.03 for one, other in within)


async def test_a_still_screen_read_through_a_steady_surface_is_one_frame_taken_once_asked():
    fake = Blocks()
    seen = Steady(fake)
    after = time.monotonic() + 0.05
    got = await seen.steady(after)
    assert got.at >= after and not seen.animating
