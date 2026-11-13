# Game (server-only experiment)

Plays a static grid game shown on a screen for the person, as one session over a task: observe,
estimate the state, plan, send one checked input, confirm its effect, go on. The plan, its contracts
and the stages still to come: [implementation plan](../../../docs/plans/2026-11-09-generic-game-plugin-implementation.md).

**Status: a server-only opt-in experiment, not a feature.** It is off unless
`AGENTIC_RUNTIME_PLUGINS` names `game`; it has no phone code and is in no APK (plan §11.3).
An explicitly configured task can read a registered browser layout, propose a move, and on a
supported hostkit execute a key or drag under bounded approval and visual prerequisites, then verify its
effect from the next screen. Optional task vision budgets enable model correction of unknown cells.
Experimental block drag omits the pre-release preview and accepts that cancellation may place a piece.
Unsupported work stops here, never falling through to generic tools. This is local implementation
and synthetic-browser validation, not a main merge, deployment or real-site gameplay claim.

| Part | Where | What |
|---|---|---|
| Manifest | [plugin.toml](plugin.toml) | hosts `server`; `[provides]` the `game` specialist |
| Specialist row | [blueprint/agents/specialists.json](blueprint/agents/specialists.json) | read only by this plugin |
| Family descriptors | [blueprint/families/](blueprint/families) · [schema](blueprint/family.schema.json) | data about each family (board, what each state field means, actions, scenes, meta actions, rule parameters with the values a profile may give them, policies); English, no site or app names |
| Hooks and task binding | [plugin.py](server/src/game/plugin.py) · [task.py](server/src/game/task.py) · [task schema](blueprint/task.schema.json) | registered-profile task start/resume, frozen identity/target/budget, session checkpoint and localized outcomes; failure stops general-tool fallback |
| Requests in words | [intake.py](server/src/game/intake.py) · [setup_game.md](blueprint/prompts/setup_game.md) | the game and goal a routed request names, from one checked model answer; the game's site found or opened |
| Session | [session.py](server/src/game/session.py) | the one loop for one move and whole games; the safety conditions of plan §6.3 |
| Contracts | [contracts.py](server/src/game/contracts.py) | plan §5, including `ActionPlan.steps`, `verify_every`, `InputReceipt.delivered_steps`, `GoalSpec.allowed_meta_actions` |
| Families | [catalog.py](server/src/game/catalog.py) · [rules/](server/src/game/rules) · [solvers/](server/src/game/solvers) · [families/](server/src/game/families) | `block_placement`, `minesweeper`, `slide_merge`: each a pure rule model `rules/<id>.py` (rules, legal moves, transition check), the session's trusted check, and a pure solver `solvers/<id>.py` (its registered policies), beside its host pieces in `families/<id>/`: `reader.py` (screen reading, profile and correction checks), `controls.py` (a planned action as one input: a key, a drag or a tap, the profile's `controls.kind`) and, for a family set up without a registered profile, `discover.py` |
| Perception | [perception.py](server/src/game/perception.py) · [profiles.py](server/src/game/profiles.py) · [profile schema](blueprint/profile.schema.json) | bounded PNG decoding, measured-layout and frame checks, family colour readers, page-element readers (`reading.source: dom`) and evidence-bound correction validation |
| Measured geometry | [geometry.py](server/src/game/geometry.py) · [pieces.py](server/src/game/pieces.py) | the board found by code from the profile's frame colour, the rest laid out relative to it and confirmed on the same image; tray pieces read from a band and split by background |
| Session profiles | [discovery.py](server/src/game/discovery.py) · [discover_screen.md](blueprint/prompts/discover_screen.md) | a game request naming a family instead of a profile (`input.game.family`, or words naming a site no profile registers) gets a profile from a model's look while the session calibrates (family, board box, grid size, cell values), kept only when the frame's lines, the cells' colours and a read-back confirm it; saved with the task (`game_profile`), its first three moves must each do what the rules predict, and they time the game's animation; its palette is `exact` (a cell matches a value by all its faces, so a value not yet learned reads unknown, never as a near one); `slide_merge` only |
| Model proposals | [vision.py](server/src/game/vision.py) | a budgeted read-only model component: corrections of unknown cells, and what a screen with no readable board shows |
| Replay | [environments/replay.py](server/src/game/environments/replay.py) | a simulated game from a seed, with scripted faults, for tests and offline evaluation; it shows the session only what the game's screen would (`shown`: no mine positions) |
| Web observation and mapping | [environments/web.py](server/src/game/environments/web.py) · [execution.py](server/src/game/execution.py) | authorized screenshots, bounded visual key/drag input, post-input state verification and receipt lookup |
| Site adapters | [adapters/](server/src/game/adapters) | how one site's game is read and played (`SiteAdapter`): where the board is, how a look reads into the rule model's state, which input plays a move, settling; the web environment calls only these. `reference.py` wraps the registered and session profiles, readers and controls (adaptive perception plan §4.3) |

## What the session guarantees

- The intent (action id, plan, the state it was made on) is saved in the task state before an input
  is sent. An input whose fate is unknown is never sent again: after a restart its action id is
  looked up, held input is released and the screen is read; the next move is planned afresh.
- One look per move: a result that is the predicted move (after the profile's `animation_s`) is the
  next move's state and its input observation. The surface re-checks that observation's pixels and
  document right before sending; one that expired or was refused is read again, and a changed board
  or surface drops the plan. A result that was not the predicted move is never carried over.
- An input the surface refused before sending any of it (the page changed under it: an advertisement
  reloading beside the board) never reached the game: it is not a move without effect, nothing is waited
  for or looked at for its result, and the next is planned from a fresh look. `MAX_REFUSED` of them in a row end the session as `needs_recovery`
  (`input_refused`).
- A changed reader geometry drops a plan even when its cells are unchanged. An observation from
  another target cannot authorise input or confirm a move's effect.
- Cancel and a lost lease stop new input and ask for a release. A release nobody confirmed stays
  `cleanup_pending`; a release is input neutrality, never the game put back.
- An unreadable cell is never played as empty.
- Delivery, effect, goal and end are recorded separately; outcomes are `goal_reached`, `game_over`,
  `budget_exhausted`, `user_stopped`, `needs_recovery`, `unsupported`.
- The session record's `phase` is `calibrating` until a look first reads the board whole (the board
  measured, a model asked where it is, a cover closed), then `running`, then `ended`. No move is
  planned while calibrating, and the switch is saved before the first one. A session that ends while
  calibrating having sent nothing (no input, none in flight, no release pending) ends `unsupported`, not
  `needs_recovery`: there is nothing of it to recover. Both phases resume after a restart.
- Only one-step plans checked after every move run today; a longer plan, a later check or a meta
  action the goal does not allow ends the session as `unsupported`.
- What stands in front of the game is got past by the session itself, at the one button the model found
  for it: a notice closed (`close_cover`), a request to agree to the site's privacy terms refused
  (`decline_consent`), and the game's own start screen started (`start_game`) only while no board was read
  yet. A consent request that offers no refusal is agreed to (`accept_consent`) only once the person,
  asked then in the approval naming the site, allows it; their refusal ends the session as `user_stopped`
  (`consent_declined`). Each is an input named in the approval, saved before it is sent, counted against
  the budget and never sent again after a restart. A page that keeps covering its board ends the session
  as `needs_recovery` (`board_covered`) after `MAX_COVERS`.

## Families

| Family | Rules (parameters) | Baseline | Search |
|---|---|---|---|
| `block_placement` | board size, row and column clears together or rows/columns only, turning, tray size; a fresh tray is accepted, never predicted | 1-move greedy | the remaining pieces in every order, best 3 moves per level, node budget |
| `minesweeper` | grid size, mines, flags (off: every move opens a cell; a flag the game shows counts as a mine); a covered cell (9) is never unread (`null`), and a lost game shows the mine opened (12) | a cell each number alone proves safe, else the free cell nearest the middle | also what numbers prove together and the mines left, then the cell the fewest agreeing layouts mine, node budget (replay: 96.5% of 9×9 games won, 88% of 16×16) |
| `slide_merge` | grid size, target tile, start tiles, spawn values; a new tile is checked against the set of tiles the game can add | first legal of down, left, right, up | expectimax, one move deeper while the node budget allows |

A rule model and a solver use only the standard library, read and write JSON, have no IO or clock, and
break every tie in a written order: they are what moves to a Rust crate later (plan §3.2, §11.4). Their
cases are the shared fixtures in [tests/fixtures/game](../../../tests/fixtures/game).

A family is checked when it is loaded (`catalog.descriptor`): its rule model offers every function in
`rules.RULE_FUNCTIONS` (the session's, the replay game's with `shown`, its state without what the game
hides, and the replay's faults: `hidden_state`, `stray_state`, so the replay assumes no board shape) and its
solver `choose` with the `POLICIES` its descriptor lists, its descriptor's `state_fields` name exactly the
fields the rule model writes, and its `rule_parameters` give each rule the rule model implements a JSON
schema of the values a profile may set. Each of its `meta_actions` has the label the input approval reads
(`policy.input_meta.<id>` in every language), so the person never reads a key. The shared profile schema names no family and no rule: a profile's
`family_id` must be a loaded family and its `rules` must fit that family's schemas. The correction
prompt carries no family's rules either; what an unread value looks like (block placement's unread
piece `[]` beside a placed slot `null`) is in `state_fields`, which the model receives with the
descriptor.

## Packaging and hand-off

Blueprint files ship inside the wheel and are resolved through `kernel.blueprint.plugin_fragment`,
shared with `care_plan` and `remote_agent`. A planned hand-off without an environment raises
`PauseSignal(give_up)`, stopping the entire turn instead of returning an error to a planner that
could try generic screen tools. Waiting exceptions pass to the host, which records `plugin_owner`;
a restart with that plugin off fails before general work. The plugin never writes ownership itself.

## Image perception (stage 7, first increment)

`perception.estimate(observation, png, profile)` returns a `StateEstimate` or `NeedObservation`.
Profiles are validated before use: unambiguous palettes, grid dimensions consistent with the family's
implemented rules and a layout relative to the board. A profile reads a screen only once measured
(below); the PNG's size and opacity must then match that measured layout and the board's frame must
still surround its measured cells, and a screen that no longer does is measured again, never read with
a stretched or guessed layout. A sample names the nearest registered colour
only when it is within reach (`MAX_REACH`) and clearly nearer than any other value's
(`DECISION_MARGIN`); a one-colour palette (piece tiles) keeps the profile tolerance. Every
sample of a cell or region must name the same value: a light that tints a tile still reads, while a
sample between two values, of another value or of no registered colour leaves it unknown (a held
piece over the board is told apart only by such samples).
An unknown board cell is `null`; an unread tray is `[]`, distinct from a consumed slot (`null`).
The estimate carries the measured layout's fingerprint (`geometry_ref`) into the common session.
No pixel marks the scene (plan §4.4, "장면 판정"): a screen read from its pixels is `playing`, and
what the game's own message covers simply reads unknown. The end is told by the rules, the page or a
model's look, in that order:

- *Rules.* The family rule model judges a readable board with no move left as the end (`status`). After a
  move whose result is partly unread (an end banner over the tray), `ended` asks whether every way
  the rules fill the unread part leaves no move: for blocks, the pieces that move left fit nowhere on
  the board the screen shows; for slides, any new tile on any cell the slide left empty leaves no
  direction. The session then ends as `game_over`; the unread part never decides a move. A move that
  used the last piece leaves a fresh tray nobody can predict, so it shows nothing.
- *Page.* A profile reading the page names its scene element (`reading.scene`).
- *Model.* When the same partly unread state shows twice in a row, the bounded correction below may
  say it is one of the family's scenes that stop play (its descriptor's `scenes`), keeping every known
  cell; it cannot say a screen the code read whole has ended. A whole screen on which the code finds or
  reads no board at all, shown with the same pixels by two looks in a row, is put to the model as a
  question of its own (`classify`, "Screens with no readable board" below).

A tray band crossed by a mark wider than any piece (an end banner) is unread as a whole: no slot is
taken for used.

The two bundled profiles (`reference_block`, `reference_slide`) are **fixture-only synthetic
layouts**, not profiles verified on real sites. Their six saved PNGs and expected states are in
[the screen fixtures](../../../tests/fixtures/game/screens/README.md). Score stays unknown.

An optional correction proposal must match the observation ID, image SHA-256 and profile geometry.
It may fill unknown cells or an unknown piece, and name a family scene that stops play for a screen the
code left partly unread, but cannot override known cells, a consumed slot, score, rules or geometry. Conflicts require another observation. The pure reader makes no
model calls. The separate correction component below requests a proposal when a task supplies a vision budget. The reader uses Pillow (pinned in the optional plugin's wheel); the pure rule models
and solvers remain standard-library-only.

## Page-element reading

A profile whose game draws its board as page elements may declare `reading` (`source: dom`, plan
§11.1): selectors for the scene element and its classes, the score element, and the family's own
part (for `slide_merge`, the tile selector, value and cell class patterns, axis order, origin and the
class of a merge result). The adapter asks `browser_shot(for_input)` for those elements and a picture
of the board alone in one observation (`kernel.observation_scope`, [contract](../../../blueprint/api/visual-input.md)):
no script of the plugin's runs in the page. The session's first look, and a look after the page and the
board's pixels disagreed, take the whole screen instead, to measure the board on. The family reader checks the page like pixels: one value
and cell per tile, in range; a cell several elements claim is known only as the declared merge
(the game keeps merged tiles until the next move); the board's pixels in the same observation must not
show another value. Anything else is unknown. The input's evidence is that board picture: the surface
compares its pixels and the whole document right before the key. `ovolve_2048` reads its page (since revision 4);
its image reader remains for the saved captures. A family adds page reading in its reader
(`check_reading`, `dom_queries`, `read_dom`) without changes to the common code.

## Measured geometry (stage 7.5)

A profile registers what stays the same wherever the game is shown (plan §4.4): its family and rules,
palettes, controls and pickup in board cells, the colour of the board's frame (`board.frame`: border
and gutters, with the cell lattice's inset from its edges; for a frame drawn sunk or raised, its right and
bottom sides' own colour, `opposite`, which the frame check reads there), and the tray relative to the board (`at`:
left and width in board widths, top and height in board heights). It registers no screen size, no
pixel position and no mark beside the board: such marks (anchors, a scene sample) were drawn at
another scale than the board or covered by the game itself, and were removed in the second increment.

`geometry.measure` finds the largest areas of the frame colour on the screen and takes each, largest
first, as a candidate board; `geometry.confirm` accepts one only when the layout it implies lands on
whole pixels inside the screen with no regions overlapping (`profiles.measure`), the frame's colour
surrounds the cells on every side (most samples halfway across each border, since a tile's light may
tint a few), at least `REGISTERED_SHARE` of the cells show the profile's own colours, every board
cell's samples agree (not checked for a profile that reads its cells from the page: their colours play
no part there), and every tray slot reads. Every later read checks the frame again: a board that moved
in a window of the same size is measured again, never read off whatever lies where it was. A cell may
show one flat colour the palette lacks (it stays unknown); a cell whose samples split refuses the
layout. Cells must be square within `CELL_SKEW`: the part of a board left showing under a dialog,
its gutters taken for its frame, would otherwise be measured as a whole board with squashed cells. A
screen that confirms no candidate (another game, a loading, tutorial or covered screen, a held piece
over the board) is refused. `confirm` is also the check a model's proposal of the board must pass
(`geometry.locate`, "Screens with no readable board" below).

The confirmed layout is the session's geometry. The web adapter measures it on the first look, saves
it in the task state (`game_geometry`: viewport, board, its `geometry_ref`) and binds every estimate,
plan and input to that `geometry_ref`. A look the geometry no longer reads (the window resized, the
board moved) is measured again before a move; a new layout replaces the old one, drops any plan made
on it and is saved. A move's own result is judged only on the geometry it was played on: a look after a
move never measures again, so a resize under a move leaves its effect unknown. A restart resumes on the
saved geometry, refused when it no longer lays the profile out the same way. Three looks in a row with
no board to measure before any geometry stop the task as `unsupported` (`board_not_found`) before any
input. The input approval lets game inputs land only in its `region`, the box around the measured board
and tray, and meta actions (closing a cover, a message's button) in its `meta_region`, the whole screen;
before any board is measured it approves taps alone. A board measured anew in another place or size
changes the approval, so the person is asked again for the inputs still left. These fields (`tap`,
`meta_region`) are the input-session authority's (#3643, plan §12 전체 진행 순서 1).

A tile in a colour the profile does not list is learned in play, from the rules rather than the
picture: when a move's result reads everywhere as the move predicts (the slid grid and at most one new
tile) except cells the move filled with a value no colour stands for yet (the family's `explain`), and
two looks in a row show those cells in the same colours, each sample point's colour joins the session's
palette as that value (`perception.colours`). A colour too near a registered one to be told from it by a
clear margin, a sample spanning an edge or numeral, or a look that disagrees teaches nothing. Learned
colours are saved with the session's geometry (`learned`) and change its `geometry_ref`; a game that
starts with such a tile on the board cannot learn it (no move made it) and leaves it unknown.

Block placement trays are read from the profile's `tray_band`: the background of each row is read off
the band's two edges (so the page's gradient at another size is still its background), a piece is the
run of columns showing anything else, split from its neighbours by background, and it belongs to the
slot its middle lies in, however far past that slot it reaches. A run between slots or wider than any
piece, or two in one slot, leaves the slot or the tray unread, never consumed.

## Bounded corrections

`vision.CorrectionReader` first runs the code reader. Only a screen read with the measured geometry
and left with unknown cells or pieces can reach the model. The host must explicitly supply an authorized screenshot,
tenant/user/task identity, `VisionBudget`, a durable `save` callback and current-observation/cancellation
checks. `current` must check the task lease, target, surface generation and observation expiry,
not just the task's existence. The task owner must serialize its readers and restore the saved
record on resume, not create a fresh budget or competing owners.

- The request uses the host's browser-purpose operator model through `complete_private`, with no
  tools, one step, an output-token cap and a deadline. No separate model name or credentials are
  embedded. Raw screenshots and responses are not persisted by this component or sent to model traces.
- Each logical request reserves one call and its full output-token cap before dispatch. Failure,
  timeout, cancellation or a board measured anew does not refund it. The task scope and limits must match on resume; an already
  attempted capture is not called again, including after a crash. The limits cover logical router
  calls and requested output tokens, not a currency budget or the provider's internal retries.
- Currentness and cancellation are checked before reading, after reservation and after completion.
  A late result, duplicate JSON keys, unsolicited tool calls, oversized output or evidence conflict
  is refused. An unresolved cell stays unknown. Task cancellation propagates.
- The PNG digest binds correction evidence only. It is **not** the raw RGBA visual-input hash and
  must not be substituted for the surface's input precondition.

The model boundary and task/session connection are tested with doubles and synthetic images; the
real-model measurements are under "Screens with no readable board".

## Screens with no readable board

A whole-screen look on which the code finds or reads no board (a dialog over it, the game's own end
or win message filling its place, another page) is asked about only when the next look shows the same
pixels, the screen has shown no readable board for `UNREAD_ASK_S` however its pixels move (an
advertisement animating over an end screen), or it is the last of `MEASURE_ATTEMPTS` before a board
never found ends the game. A frame caught mid-animation costs nothing. A scene that stops play, once
seen, holds while no board reads again, so moving pixels over it are not asked about again; a cover's
button is always the one found on the pixels asked about (`CorrectionReader.classify`,
[classify_screen.md](blueprint/prompts/classify_screen.md)). It uses the same budget and attempt
record, for screen-read and page-read profiles alike. Code checks the answer (`perception.screen`): it
must name this capture, and be `board`, one of the family's scenes that stop play, `covered` or
`unknown`, never `playing`. A board box is taken only for `board`, a button box only for `covered`,
both inside the screen, the button at most half its width and a quarter of its height.

A board the code reads whole may still stop taking input: a game may show its win notice beside a board
that stays visible. A delivered move without effect has the next look (`STALLED`) ask the same question
of the whole screen once per such board, saying the board was read (`board_read`). Only an answer naming
something to press counts: a scene whose button the family presses becomes that scene over the board as
read (the person is asked before its button, as above), and a cover is closed; either accounts for the
move, which is then no failed recovery. Anything else leaves the move without effect counted as before,
and a third unconfirmed move ends the game only after that look.

A question takes seconds on a real model: longer than a look stays valid for input
(`INPUT_VALID_S`) and longer than the profile's settling time. The look waits for the answer, bounded
only by the vision budget's timeout and the task's time, and the answer is kept for the pixels it was
about (`CorrectionReader.judged`, `corrected` in the web adapter): the next look of the same pixels uses it without
asking again, and any input it leads to goes out on that newer look. What a screen was judged to show
is saved with the task's vision record (`screens`, by the decoded image's digest), so a run restarted
after the input approval's wait closes the same popup without asking about it again.

- `board`: the code did not find a board the model sees (a panel in the frame's colour touching it
  joins its frame, so no candidate confirms). `geometry.locate` looks near the model's box only: each
  edge is the first line from outside the box, within `EDGE_REACH` of its edge, that the frame colour
  covers across at least `EDGE_SHARE` of the box's span (a score box touching the board covers less),
  and the board inside it is checked by `confirm` like any other. A confirmed board becomes the
  session's geometry for the next look to read, before a move only; a box that confirms nothing
  changes nothing, and the usual three looks end the task as `board_not_found` before any input.

- A scene becomes that scene's state with nothing under its message read (the rule model's `unread_state`),
  judged with no input: `over` ends the game, `won` reaches a goal only as the family says.
- `covered` with a button is marked on the observation, and the session closes it: a press and release
  in place at the button's middle (a tap: a `browser_drag` that neither moves nor holds, `execution.tap`),
  sent with `input_meta_action: close_cover` under an approval whose `input_kinds` include `tap`, whose
  `allowed_meta_actions` name `close_cover` and whose `meta_region` holds the button. The input's visual
  guard is the button itself, and the hostkit's path checks (no iframe, field or protected element under
  the pointer) still apply. The approval names each meta action by `policy.input_meta.<name>`.
- Anything else stays unread, and the usual limits end the session.

Measured with real models through OpenRouter (2026-11-12, `tools/evaluate_game_live.py
--vision-model`; plan §12 "단계 7.5 4차" has the numbers): `anthropic/claude-haiku-5.5` answered six
saved screens right (two boards joined by a frame-coloured score box, a plain board, two end screens,
a dialog with a close button), its board boxes within a few pixels and each confirmed to read as the
code reads the clean screen, in 2 to 3.5 s. Live on play2048.co at 1024×800 it judged the first-visit
popup `covered` and boxed its X; the session closed it through Sentinel and the hostkit and played on.
`google/gemini-omni-1.2-flash` boxed boards wrongly (every one refused by the code) and boxed a close
button beside it: a button box is checked for size and place only, so a model that misplaces it would
have the session click there.

## Registered website readers

`ovolve_2048` and `blockblast_org` add two explicitly registered desktop layouts. Their
[actual browser captures and independent answers](../../../tests/fixtures/game/sites/README.md)
cover initial/progress states, a consumed tray slot, a full terminal board, the two games' end
screens (2048's overlay over the board, Block Blast's "No Space Left" banner over the tray), and
refusal of loading, tutorial and held-drag screens. `play2048_co` (the official 2048, a canvas board read
from its pixels) adds tile colours up to 2048 with their vertical ramps, its win and end screens, which
move the board and so leave it unread, and a mid-slide refusal. Its page carries the ad network's open
shadow roots, which the hostkit's input evidence walks like the page (only a closed root is refused), so
it plays. They are
development/regression evidence, not unseen-site evaluation. The two `reference_*` profiles remain
synthetic and fixture-only.

`tatham_mines` (Simon Tatham's Mines, a canvas board, 9×9 with 10 mines) is the `minesweeper` family's
registered site. The family reader reads each cell's middle, clear of its edges: the face most of it shows
tells an opened cell (palette 0) from a covered one (9), the ink most of the rest shows is the digit (1 to
8, counting only pixels of an ink itself, since a stroke's blended edges pass through other digits'
colours), any ink on a covered face is a flag, and a face like neither (its `any` entry) is the mine
opened, often the same red as a digit. Its board frame is sunk (grey top and left, white right and bottom).
Ten captures whose mines were set by the game id, with answers worked out from the mines by the rules,
cover every digit, a win (the game flags every covered cell), a loss and the flash each starts with, which
reads unknown or as several mines opened and is refused. Played live on 2026-11-13 with no model: nine
games (eight won, one lost on a board set by its game id to need a guess), 107 moves, each confirmed, none
refused, every end the one the page states. Its status bar's clock, which changes every second away
from the board, no longer refuses input (`blueprint/api/visual-input.md`).

Registered profiles require `source.url`, which is provenance, never task routing or input
authority. A palette entry can declare alternate colours for the same meaning, while overlap
between different meanings is rejected. Tray `piece` data declares a cell size relative to a board
cell and the band's expected background: the reader checks solid coloured interiors, their grid and
every pixel outside the inferred tiles. Unknown colours, holes, stray pixels or misaligned cells leave
the piece unknown instead of shrinking it or marking its slot consumed. `blockblast_org` revision 7,
`ovolve_2048` revision 5 and `play2048_co` revision 2 are the measured-geometry form; the same captures
and new ones at other window sizes (1100×780 and 1440×1000 for Block Blast, 1440×1100 for play2048.co)
read the same answers, and a one-by-six piece wider than its slot reads.

These profiles read playing layouts and the end screens above; a score is read only from a page
element (`reading.score`), never from pixels, and Block Blast's later game-over and advertisement
screens are not parsed.
They do not identify a site automatically, support arbitrary skins or prove safe input. Site
registration comes first; a registered site always plays its registered profile. A `slide_merge` game on a
site no profile registers is set up from its screen (`discovery.py`, plan §4.5); it reads the registered
2048 captures as their profiles do and was played live on play2048.co without its profile. Playing
without site registration is still to be evaluated on held-out sites (plan step 8) under the same
approval and budget limits. The common visual-input change is incorporated in this working tree; no main merge or deployment is implied.

## Execution and recovery

Key-controlled profiles use `browser_visual_input=1` and `browser_key_input>=1`; drag profiles
use `browser_visual_input=1` and `browser_pointer_input>=1`. The adapter verifies
the delivered PNG's RGBA digest, dimensions, observation reference, executor clock and epoch.
The surface performs the authoritative fresh-pixel/focus/target/TTL check before sending, once right
before the first event ([visual input](../../../blueprint/api/visual-input.md)): a key needs the page's
layout unchanged but not its text, a tap only what lies under it, so a timer beside the board (Tatham
Mines) refuses neither. Frame sampling and browser input are not atomic. A task must provide `budget.max_seconds` for live input.
Missing capability or budget remains unsupported; existing ordinary key tools are never a fallback.

The task asks for bounded approval before recording an input intent. Approval suspension preserves
the session and model budgets and resumes on a fresh frame. `SurfaceHandle.invoke(input_session=...)`
reserves the authority's action/cost before dispatch. Each input intent is checkpointed before send,
and its effect is checked afterward. A last permitted move that reaches the goal reports success
only after its observed transition, rather than ending as budget-exhausted on the next iteration.

A lost reply is reconciled with `browser_shot(input_action_id=...)`, not another input call. The
runner returns only this trusted work/current tab/epoch's receipt. Its confirmed key release can
establish neutrality; a missing/foreign/unknown receipt cannot. There is no force-release shortcut.
Receipts are in executor memory only. A changed executor or host clock requires recovery; restarting
must not reset the authority, action or model budgets. Unknown cleanup retains the intent.

Drag profiles play without a pre-release pixel preview; the logical preview cells in a mapped proposal
are metadata, not pixel hashes. Cancellation stops new input; releasing an in-flight drag may place a
block. The runner records unknown delivery and `drop_possible:true` after interrupted dragging;
successful release proves input neutrality, not rollback. A drag the runner refused before any event
comes back as a bound rejection, never as unknown delivery.

The common session samples family state with a bounded settling window (default 4 seconds,
150 ms between reads, 250 ms of unchanged state). Profiles can supply `settling` within schema
bounds, including `animation_s`, waited after each input before its result is looked at (`ovolve_2048`:
0.65 s, its score's 600 ms animation), and `still`: a result counts only once two looks in a row also
show the same pixels where input is checked (`Observation.image_ref`, a digest of the guarded regions),
for a game whose effects keep fading after its state is already the predicted one (`blockblast_org`:
a cleared line, a fresh tray sliding in; at 1100×780 it took input refusals from 10 in 40 moves to none).
A result that is the predicted move is taken at once (or on its second, unchanged look); otherwise
board/tray/scene state, geometry and surface identity must stay unchanged for the stable time; whole-frame
stillness is not assumed. An absent or inconsistent post-input effect remains provisional until the window
ends. Waits check cancellation, lease and elapsed-time budget, while the final allowed action
still receives verification. Unknown input neutrality stops before reading a held piece as consumed.
Failed captures clear input authority but retain the last valid PNG as diagnostic evidence.

Drag `pickup` specifies the held anchor (`grab` or a fractional bounding-box point), held cell scale
relative to board cells, and cursor-to-anchor offset in board-cell units. Actual tray pixels select
the solid source cell; the held geometry determines the cursor endpoint. Width/height, non-origin
first cells and the measured board scale are included. The Block Blast values come from captured
one-row and three-row held pieces, not a shared pixel offset. They remain registered-profile data,
not automatic runtime pickup discovery; every released move is checked against its expected effect.

## Known gaps

- Preview validation and no-drop cancellation remain deferred. Region-scoped exact pixel checks
  allow unrelated Canvas motion; a key still refuses on any layout or attribute change in the page (a
  clock drawn by class, an advertisement swapping its elements), and a pointer on any under its path.
- [Bounded real-site validation](../../../docs/reports/2026-11-11-game-stability.md): final 2048
  run confirmed 10/10 delivered moves; final Block Blast run confirmed 1/2 before game-over and
  advertisement overlays made the profile unreadable. Earlier diagnostic runs are separate.
  This is not evidence of unrestricted gameplay, unseen-site generalization, deployed-server or phone E2E.
- A board drawn as pixels has no score reading (only a page element is read), and a session profile is
  made for `slide_merge` only. Geometry is measured by code from the frame colour;
  a window size where a region's sample falls on something that does not scale with the board (the
  page's shadow under play2048.co's board at 1024×800) is refused, not adapted. A game that starts on
  play2048.co's end screen (which moves the board) is measured as a playing board.
- Phone input follows in stage 11; the phone host follows in stage 13.

Tests: `tests/test_game_*.py`, including `test_game_live.py` for task/model integration and local
Chromium, plus `tests/test_browser_visual_input.py` and `tests/test_input_session.py` for input
and authority. These do not imply external-site or device validation.

## Requests in words

Work the chat routes to the `game` specialist carries the person's words, not a game request. Before
anything else, `intake.resolve` turns them into one and saves it as `game_request`:

- One answer from the game specialist's tier (`ModelTier.OPERATOR`, the main model at low effort: the
  router tier's small model took a score asked for in a 2048 game for a tile; no tools, `SETUP_TOKENS`,
  `SETUP_TIMEOUT_S`), read as the JSON object it gives after any reasoning (`outer_json`),
  given the task's instruction, the person's own words and the registered games (each profile with
  its site and its family's summary), names the game and the goal ([setup_game.md](blueprint/prompts/setup_game.md)).
  Code takes it only for a registered, non-fixture profile, a goal from the objectives and a number the
  goal needs and nothing else (`intake.checked`). The budget is never asked for: `DEFAULT_MAX_ACTIONS`,
  `DEFAULT_MAX_SECONDS` and the family's `default_compute`. Each game is given with its family's
  `goal_value` (what a target counts: the largest tile, the score), and a profile with `offered: false`
  (a development site beside the game's own, `ovolve_2048`) is not offered, so "2048" names one site.
- The game's page is a page of the task already on the profile's site (`browser_list_tabs`), else the
  site opened with `browser_open` under the ordinary permission check; no other navigation is made.
- The choice is saved before the site is opened and the request with its page after: a run restarted
  after the approval to open the site opens it without asking the model again, and every later run
  plays the saved request.
- Words that name no registered game (`game_not_named`) end the task as `unsupported` before any
  surface is asked for, telling the person the registered games' sites (`game.not_named`). No model,
  or an answer that is not a request, ends it as `unsupported` too.

## Explicit task input

A host may instead supply `input.specialist: "game"` and a schema-checked `input.game`, for example:

```json
{
  "profile_id": "ovolve_2048",
  "tab_id": 7,
  "goal": {"objective": "actions", "target_value": 1},
  "budget": {"max_actions": 5, "compute": 50, "max_seconds": 60},
  "vision": {"max_calls": 3, "max_output_tokens": 3000, "tokens_per_call": 1000, "timeout_s": 4}
}
```

`vision` is optional: by default each question is bounded (`tokens_per_call`, `timeout_s`) and a game is
never stopped for how many it needed (`max_calls` and `max_output_tokens` are null); give them to cap a game.
The tab ID must come from this task's browser observations. Fixture-only profiles, unimplemented goal scopes/meta actions,
invalid budgets and unknown policies are refused. An optional `policy` must be declared by the family.
The profile's `source.url` is not consulted as authority; it is opened only for a request in words.

`game_context` binds tenant/user/task, request, surface, the profile's fingerprint (`profile_ref`) and
host monotonic-clock lifetime; `game_geometry` holds the session's measured geometry;
`game_session` preserves the session's counters, goal, time origin, intent and outcome. Host suspension
propagates and resumes the saved record. Changed scope/geometry/budget/clock or surface requires
recovery rather than a new budget. An ended record is not played again. Legacy unbound intents are
preserved for recovery; unavailable receipt/cleanup support remains unknown, never a successful abort.

`vision` is optional: without it the task gets `DEFAULT_VISION` (task.py). `game_vision`
stores reservations before model dispatch, and the screens judged, and survives approval waits. An
async task guard checks the lease/cancellation around reservations and responses; the model wait is
bounded by the vision budget's timeout and the task's time. Geometry and
known cells cannot be overwritten. Model output is not an input grant.

The most recent valid capture goes through `TaskContext.keep_image`; unavailable storage is recorded
without claiming a saved file. State, proposal, bounded history and artifact references are persisted,
not PNG bytes or raw model responses. An already-met visible goal is observed success with zero moves.
Mapped proposals are never receipts: only input delivery and the subsequent state establish execution.

The generic server/Android plugin hook now retains ownership when `before_task`/`beforeTask`
parks through an approval, surface wait or waiting pause signal, as delegation already did.
Terminal pause signals release ownership. The Android change has a unit regression but was not
compiled/run locally (no Java runtime/Android toolchain); Python wire checks are not a substitute.
