"""Site adapters: how one site's game is read and played on its screen (adaptive perception plan §3, §4.3).

An adapter holds everything that differs from site to site: where the board is, how a screen or page
reads into the rule model's state, which input plays a move, how long the game animates. The web
environment keeps what every site shares (captures and their evidence, the person's approval, the
bounded model's look at a screen with no board, input and its receipt) and calls only what is listed
here. Nothing an adapter returns is input authority: a state is the rule model's to check, a move's
input is bound to its observation and approved region, and the surface checks the pixels again.

The reference adapter (`reference.py`) wraps the hand-written profiles and readers; the generated
adapters the experiment adds later answer the same calls.
"""
from __future__ import annotations

from typing import Any, Protocol

from ..contracts import NeedObservation, Observation, StateEstimate
from ..execution import MappedAction
from ..families import GridFamily


class SiteAdapter(Protocol):
    family_id: str
    # whether the adapter reads the page's elements (and the board's picture alone as input evidence)
    reads_dom: bool

    @property
    def played(self) -> dict[str, Any]:
        """The controls (`kind`, `keys`) and the settling the game is played with."""

    @property
    def measured(self) -> bool:
        """Whether the board was found on this screen: until then only taps are approved."""

    @property
    def viewport(self) -> list[int]:
        """The measured screen's size."""

    @property
    def geometry_ref(self) -> str | None:
        """The measured layout's name, which every state read on it carries; None until measured."""

    @property
    def awaits_look(self) -> bool:
        """Whether the site still needs a model's look to be read at all (a game no profile registers)."""

    @property
    def registered(self) -> bool:
        """Whether the site's reading was registered and reviewed, not made in this session: the approval says so."""

    @property
    def address(self) -> str:
        """The page the game is on, as the approval names it."""

    def family(self) -> GridFamily:
        """The rule model under the rule values this site plays."""

    def scope(self, measuring: bool) -> dict[str, Any]:
        """What a look captures (`environments.screen.Surface.elements`): page elements and, once measured, the board alone."""

    def measure(self, png: bytes) -> bool | NeedObservation:
        """Find the board on a whole screen: whether the layout every check confirms changed."""

    def locate(self, png: bytes, box: list[int]) -> bool | None:
        """Find the board where a model saw it: whether the layout changed, None when no check confirms it."""

    def board_picture(self, png: bytes) -> bytes:
        """The measured board's picture cut from a whole screen, as a page read pictures it."""

    def read(self, observation: Observation, png: bytes, elements: Any = None) -> StateEstimate | NeedObservation:
        """The rule model's state on this look, estimated by the rule model."""

    def target(self, action: dict[str, Any], state: StateEstimate, observation: Observation, png: bytes,
               elements: Any = None) -> MappedAction:
        """The input that plays `action` on the look `state` was read from, once the same look still reads
        to `state` on the measured layout; ValueError otherwise."""

    def regions(self) -> list[list[int]]:
        """Where game inputs land ([left, top, right, bottom] screen pixels): their approval is the box around them."""

    def guarded(self) -> list[list[int]]:
        """Where the surface checks, right before an input, that the pixels are still those observed."""

    def picture(self, png: bytes) -> str:
        """The guarded pixels as one digest: a move's result is still when it stays the same."""

    def moved(self, before: tuple[dict[str, Any], dict[str, Any]] | None) -> None:
        """The game move just sent (its state and action), or None for the session's own press."""

    def learn(self, observation: Observation, png: bytes, state: StateEstimate
              ) -> tuple[StateEstimate | NeedObservation, bool]:
        """A move's result read again with what it teaches about the site; whether the record changed."""

    async def correct(self, observation: Observation, png: bytes, digest: str,
                      state: StateEstimate | NeedObservation, reader: Any, remaining: float | None
                      ) -> StateEstimate | NeedObservation | None:
        """Cells left unknown, filled by the bounded model where it may; None when out of time."""

    def propose(self, png: bytes, look: Any) -> Any:
        """The site's reading made from a model's look for a game no profile registers, or why not."""

    def adopt_profile(self, measured: Any) -> Any:
        """Play by the reading `propose` made, measured where the look was confirmed: what the task keeps."""

    def record(self) -> dict[str, Any]:
        """What a restart needs to read this screen as before."""

    def restore(self, record: dict[str, Any]) -> None:
        """The measured reading from its record, only when it lays the same site out the same way."""
