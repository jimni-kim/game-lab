"""The hand-written screen pieces of each family (`families/<id>/`): what the baseline reads and plays
registered sites with. The agent's own loop reads by generated code and needs none of them."""
from __future__ import annotations

from types import ModuleType

from . import catalog
from .families.block_placement import controls as block_placement_controls, reader as block_placement_reader
from .families.minesweeper import controls as minesweeper_controls, reader as minesweeper_reader
from .families.slide_merge import (controls as slide_merge_controls, discover as slide_merge_discover,
                                   reader as slide_merge_reader)

# each family's reader beside its rule model: the state's BOARD key, read(image, …), tile (a value a cell may
# hold), check_profile, check_correction and, where its pages can be read, check_reading, dom_queries and
# read_dom
READERS: dict[str, ModuleType] = {"block_placement": block_placement_reader, "minesweeper": minesweeper_reader,
                                   "slide_merge": slide_merge_reader}
# each family's controls: the input KIND its profiles register, check (of a profile's controls) and
# map_action (a planned action as one surface input)
CONTROLS: dict[str, ModuleType] = {"block_placement": block_placement_controls, "minesweeper": minesweeper_controls,
                                    "slide_merge": slide_merge_controls}
# the families whose games are set up from a look with no registered profile (plan §4.5), each with
# rules (the rule values a board's size sets) and palette (its colours from the cells' values, or why it
# cannot be made); their descriptors carry `discovery`. A descriptor may carry `discovery` without a module
# here: the block also says how a family is explored while an adapter is made for it (`adaptation`), which
# needs no palette
DISCOVERY: dict[str, ModuleType] = {"slide_merge": slide_merge_discover}

for _family_id in DISCOVERY:
    if "discovery" not in catalog.descriptor(_family_id):
        raise ValueError(f"game family {_family_id}: the code sets games up from a look but the descriptor has no discovery")
