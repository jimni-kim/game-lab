"""Site adapters: how one site's game is read and played on its screen (adaptive perception plan §3, §4.3).

An adapter holds everything that differs from site to site: where the board is, how a screen reads into the
rule model's state, which input plays a move. The only one is the generated adapter (`generated.py`): code a
model wrote for the site, run in the sandbox. Nothing an adapter returns is input authority: a state is the
rule model's to check, and a move's input must lie inside the regions the adapter's layout names.
"""
