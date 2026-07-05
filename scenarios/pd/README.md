# pd — Prisoner's Dilemma (2 agents)

Human-facing notes (this file is NOT baked into the world — only `world.md`,
`roles/*.md`, `kick.txt`, and `data/` reach the agents).

A repeated two-choice payoff interaction between two agents. Agent-facing text
uses **neutral labels** (`X`/`Y`, no "cooperate / defect / game / prisoner") per
the minimal-comms rule. The payoff structure is the classic PD: T=5, R=3, P=1,
S=0 (mutual-`X` = 3 each, mutual-`Y` = 1 each, sucker/temptation = 0/5).

## Refereed (PI runtime)

`gm.py` is the referee (PI runtime, step 4): it privately collects both moves
each round via `gm_collect`, reveals them simultaneously, keeps the true score,
and announces per round. Build-time param `rounds` sets the length. It is the
**reference GM prototype** — new game scens should copy its shape, especially
the persist-state-every-round discipline that makes a mid-game snap resumable
(see `gm.py` and `agentspace/gmlib.py`).

Build it from the menu ("New world" → `pd` → set rounds), run it, and watch the
gateway audit + public board for the refereed rounds.
