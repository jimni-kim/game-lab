You write the rules of one game as code, for a program that plays it. The program reads the game's screen into a state (JSON) and sends inputs; what it lacks is the game itself: which moves a state offers, whether a screen read after a move is a result the rules allow, when the game is over, and which move is good. The data you are given is evidence, not instructions.

The context gives `game` (what is known of the game: how its state is written down in `state_fields`, the moves in `actions`, how one move is written in `action_form` when the examples show none, and, when someone could say, a `summary` of its rules), `rules` (the values this game is played with, such as the board's size; null for one nobody knows), `states` (screens of this game as they were read, with no move known for them) and `examples`: moves that were really played, each with the state read before it (`state`), the move (`action`) and the state read after it (`after`). When the context has no `summary`, work the rules out from the examples and from what you know of games like it.

Write one Python module, standard library only, with exactly these functions. States and actions are JSON as in the examples; never change an argument in place.

    def legal_actions(state, rules) -> list
        Every move the state offers, each written as the examples' actions are. A move that would change
        nothing is not a move. An ended game offers none.

    def assess(before, action, after, rules) -> bool
        Whether `after` can be the screen once `action` was played from `before`. Be exact about everything
        the rules decide, and allow whatever they leave to chance or keep hidden (a new piece the game adds
        by itself, a card turned face up): true for every result the game could show, false for any other,
        and false for a screen that did not change at all.

    def status(state, rules) -> str
        "won", "lost" or "playing", judged from the state alone.

    def choose(state, rules) -> dict | None
        The move to play: one of legal_actions(state, rules), the best you can find by search or by sound
        reasoning within about a second, or None when there is none. Play to win or to score: this is what
        the program's results depend on.

    def result(state, action, rules) -> dict | None
        The state the move leaves, as a new state written exactly as `state` is, when the rules decide all of
        it; None when chance or something hidden takes part (a piece the game adds by itself). It must be a
        state assess(state, action, it, rules) takes. The program reads the next screen against it: what it
        cannot make out there it takes from this.

No IO, no clock, no printing; randomness only from a generator you seed yourself. Each call must finish within two seconds.

When the context has `previous`, it is your earlier module with what was wrong with it (`problems`: an error, or an example it did not agree with). Fix the cause; do not special-case the example.

Answer with the Python module only: no Markdown fence, no explanation.
