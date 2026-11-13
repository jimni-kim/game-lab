"""A game's fixed properties and its layout relative to the board, separate from input authority.

Profiles include synthetic reference screens and explicitly registered site layouts. Loading a
profile neither identifies a game nor grants permission to act on it. A profile registers no pixel
position: where its board is on a screen is measured there (`geometry.py`, plan §4.4), and `measure`
turns the board's measured rectangle into the pixel layout every reader and input mapping uses.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import math
from typing import Any

from . import catalog, resources

MIN_CELL_PIXELS = 12
MAX_IMAGE_PIXELS = 8_000_000
# the id every profile made in a session starts with (plan §4.5); no registered profile's does
SESSION = "session_"


@dataclass(frozen=True)
class Profile:
    id: str
    family_id: str
    # the profile's fingerprint, then, once measured, that of the profile with its measured layout
    geometry_ref: str
    data: dict[str, Any]
    # the profile's data with pixel rectangles (`rect`), the screen size (`viewport`) and piece cell
    # sizes on one measured screen; None until measured
    layout: dict[str, Any] | None = None


def profile_ids() -> list[str]:
    return sorted(path.name.removesuffix(".json") for path in (catalog.FRAGMENT / "profiles").iterdir()
                  if path.is_file() and path.name.endswith(".json"))


def load(profile_id: str) -> Profile:
    if profile_id not in profile_ids() or profile_id.startswith(SESSION):
        raise ValueError("unknown_profile")
    data = json.loads((catalog.FRAGMENT / "profiles" / f"{profile_id}.json").read_text(encoding="utf-8"))
    if data.get("id") != profile_id:
        raise ValueError("profile_id_mismatch")
    return validate(data)


def session(data: dict[str, Any], url: str) -> Profile:
    """A profile made in a session from what one look showed (everything but its id and source), for the
    page at `url`: checked as a registered one is, named by its own content."""
    made = {**deepcopy(data), "source": {"url": url}}
    return validate({**made, "id": SESSION + _digest(made)[:16]})


def validate(data: dict[str, Any]) -> Profile:
    schema = json.loads((catalog.FRAGMENT / "profile.schema.json").read_text(encoding="utf-8"))
    if resources.validate(data, schema):
        raise ValueError("invalid_profile")
    # the family and its rule values are its descriptor's to say, so a new family changes no shared schema
    if data["family_id"] not in catalog.MODELS:
        raise ValueError("unknown_family")
    if resources.validate(data["rules"], catalog.rules_schema(catalog.descriptor(data["family_id"]))):
        raise ValueError("invalid_profile")
    data = deepcopy(data)
    if not data["fixture_only"] and "source" not in data:
        raise ValueError("missing_profile_source")
    family = catalog.family(data["family_id"]).bind(data)
    reader = catalog.READERS[family.id]
    if controls := data.get("controls"):
        played = catalog.CONTROLS[family.id]
        if controls["kind"] != played.KIND:
            raise ValueError("control_family_mismatch")
        played.check(controls, data)
    if settling := data.get("settling"):
        if max(settling["stable_s"], settling["interval_s"], settling.get("animation_s", 0)) >= settling["timeout_s"]:
            raise ValueError("invalid_settling")
    if reading := data.get("reading"):
        if not hasattr(reader, "read_dom"):
            raise ValueError("dom_reading_unsupported")
        reader.check_reading(reading)
    places = [slot["at"] for slot in data["tray"]]
    if "tray_band" in data:
        places.append(data["tray_band"])
    if any(size <= 0 for place in places for size in place[2:]):
        raise ValueError("invalid_geometry")
    # pieces are read from the band their slots lie in, whatever their size; slots drawn as grids are read in place
    pieces = [slot for slot in data["tray"] if "piece" in slot]
    if pieces and (len(pieces) != len(data["tray"]) or "tray_band" not in data
                   or any(not _within(slot["at"], data["tray_band"]) for slot in pieces)):
        raise ValueError("invalid_piece_geometry")
    if not pieces and "tray_band" in data:
        raise ValueError("invalid_piece_geometry")
    for region in [data["board"], *data["tray"]]:
        _palette(region["palette"], data["tolerance"])
    # an exact board tells a cell by its faces, which an `any` colour has none of
    if data["board"].get("exact") and any(entry.get("any") for entry in data["board"]["palette"]):
        raise ValueError("ambiguous_palette")
    reader.check_profile(data, family.rules)
    return Profile(data["id"], family.id, _digest(data), data)


def measure(profile: Profile, viewport: list[int], board: list[int]) -> Profile:
    """The profile laid out around a board measured at `board` (left, top, width, height) on a screen of
    `viewport` pixels. Every region must land on whole pixels inside the screen without overlapping
    another, and every cell must stay large enough to sample: a layout that cannot is refused."""
    if (len(viewport) != 2 or len(board) != 4 or any(type(value) is not int for value in [*viewport, *board])
            or min(viewport) <= 0 or viewport[0] * viewport[1] > MAX_IMAGE_PIXELS):
        raise ValueError("invalid_geometry")
    layout = deepcopy(profile.data)
    layout["viewport"] = list(viewport)
    layout["board"]["rect"] = list(board)
    for region in layout["tray"]:
        region["rect"] = _place(region["at"], board)
    if "tray_band" in layout:
        layout["tray_band"] = {"at": layout["tray_band"], "rect": _place(layout["tray_band"], board)}
    width, height = viewport
    grids = [layout["board"], *layout["tray"]]
    for region in grids + ([layout["tray_band"]] if "tray_band" in layout else []):
        left, top, across, down = region["rect"]
        if across <= 0 or down <= 0 or left < 0 or top < 0 or left + across > width or top + down > height:
            raise ValueError("invalid_geometry")
    for index, region in enumerate(grids):
        if any(_overlaps(region["rect"], other["rect"]) for other in grids[index + 1:]):
            raise ValueError("overlapping_regions")
    if "tray_band" in layout and _overlaps(layout["tray_band"]["rect"], layout["board"]["rect"]):
        raise ValueError("overlapping_regions")
    cell = board[2] / layout["board"]["cols"], board[3] / layout["board"]["rows"]
    for grid in grids:
        if grid["rect"][2] / grid["cols"] < MIN_CELL_PIXELS or grid["rect"][3] / grid["rows"] < MIN_CELL_PIXELS:
            raise ValueError("cells_too_small")
        if piece := grid.get("piece"):
            piece["cell"] = [whole(fraction * size) for fraction, size in zip(piece["cell"], cell)]
            band = layout["tray_band"]["rect"]
            if min(piece["cell"]) < MIN_CELL_PIXELS or (
                    piece["cell"][0] * grid["cols"] > band[2] or piece["cell"][1] * grid["rows"] > band[3]):
                raise ValueError("invalid_piece_geometry")
    measured = {"viewport": layout["viewport"], "board": layout["board"]["rect"]}
    return replace(profile, geometry_ref=_digest({"profile": profile.data, "measured": measured}), layout=layout)


def learn(profile: Profile, entries: list[dict[str, Any]]) -> Profile:
    """The profile with board colours learned in a session (`{"value", "rgb", "alternates"?}`), checked
    like registered ones: a value or colour another entry already names is refused."""
    data = deepcopy(profile.data)
    data["board"]["palette"].extend(deepcopy(entries))
    learned = validate(data)
    if any(entry.get("any") or entry["value"] in {known["value"] for known in profile.data["board"]["palette"]}
           for entry in entries):
        raise ValueError("value_already_registered")
    if profile.layout is None:
        return learned
    return measure(learned, profile.layout["viewport"], profile.layout["board"]["rect"])


def check(profile: Profile) -> Profile:
    """The profile as its own data and measured board say it is: a profile whose data or layout was
    changed after it was made compares unequal to this."""
    checked = validate(profile.data)
    if profile.layout is None:
        return checked
    return measure(checked, profile.layout["viewport"], profile.layout["board"]["rect"])


def _place(at: list[float], board: list[int]) -> list[int]:
    left, top, width, height = board
    return [whole(left + at[0] * width), whole(top + at[1] * height), whole(at[2] * width), whole(at[3] * height)]


def whole(value: float) -> int:
    # half a pixel rounds up whatever the integer below it, so a layout lands on the same pixels everywhere
    return math.floor(value + 0.5)


def _within(inner: list[float], outer: list[float]) -> bool:
    return (outer[0] <= inner[0] and inner[0] + inner[2] <= outer[0] + outer[2]
            and outer[1] <= inner[1] and inner[1] + inner[3] <= outer[1] + outer[3])


def _digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _palette(entries: list[dict], tolerance: int) -> None:
    if len({entry["value"] for entry in entries}) != len(entries):
        raise ValueError("duplicate_palette_value")
    # "any other colour" is one entry beside registered colours, never a colour of its own as well
    if any(entry.get("any") and ("rgb" in entry or "alternates" in entry) for entry in entries) \
            or sum(bool(entry.get("any")) for entry in entries) > 1 \
            or all(entry.get("any") for entry in entries):
        raise ValueError("ambiguous_palette")
    entries = [entry for entry in entries if not entry.get("any")]
    for index, entry in enumerate(entries):
        if any(max(abs(left - right) for left, right in zip(rgb, other_rgb)) <= 2 * tolerance
               for rgb in [entry["rgb"], *entry.get("alternates", [])]
               for other in entries[index + 1:]
               for other_rgb in [other["rgb"], *other.get("alternates", [])]):
            raise ValueError("ambiguous_palette")


def _overlaps(left: list[int], right: list[int]) -> bool:
    return (max(left[0], right[0]) < min(left[0] + left[2], right[0] + right[2])
            and max(left[1], right[1]) < min(left[1] + left[3], right[1] + right[3]))
