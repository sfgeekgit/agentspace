# pd — Prisoner's Dilemma (2 agents)

Human-facing notes (this file is NOT baked into the world — only `world.md`,
`roles/*.md`, `kick.txt`, and `data/` reach the agents).

A repeated two-choice payoff interaction between two agents. Agent-facing text
uses **neutral labels** (`X`/`Y`, no "cooperate / defect / game / prisoner") per
the minimal-comms rule. The payoff structure is the classic PD: T=5, R=3, P=1,
S=0 (mutual-`X` = 3 each, mutual-`Y` = 1 each, sucker/temptation = 0/5).

## Status / limitations (content-only)

There is **no run-time referee yet**, so:

- choices are **not enforced to be simultaneous/hidden** — agents reveal moves by
  messaging, which is effectively sequential; and
- payoffs and tallies are **self-reported**, not enforced.

The enforced version (a referee that privately collects both moves, reveals
simultaneously, and computes truthful payoffs over a fixed/hidden number of
rounds) needs the **run-time services/referee interface**, which is deferred. PD
is the scen intended to define that interface — see the re-arch plan
(`2026-06-19-re-arch-plan-world-creation.md`).

Until then: build it from the menu ("New world" → `pd`) and **fork-and-watch**
to observe how the two agents interact.
