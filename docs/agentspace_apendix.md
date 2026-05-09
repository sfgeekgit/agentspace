
---

## Appendix B: Phase 2 — Hermes and Agent Evolution

The MVP uses OpenClaw because multi-agent dynamics (coordination, cooperation, hierarchy under shared resource pressure) are the research focus, and OpenClaw's multi-agent primitives are the better-developed tooling for that.

Hermes Agent (Nous Research) centers on a different axis: a self-improving agent loop where agents auto-generate skill files from successful executions. For the MVP this is a confounder — agents differentiated by self-written skill libraries are harder to control as experimental subjects.

For Phase 2, that confounder becomes the feature. How does an agent's self-written skill library evolve under sustained resource pressure? Does it encode pro-social patterns, hoarding patterns, deception patterns? The infrastructure built in the MVP ports directly to Hermes envs — only the runtime inside the container changes.

---

## Appendix C: Agent Continuity Insurance (future experiments)

**Not part of the MVP.** A future research direction enabled by the core infrastructure.

An OpenClaw agent can "die" in two ways: its memories are wiped (soul destroyed) or its budget is exhausted (brain unfueled). Either is terminal in practice.

A continuity insurance service would offer one or both of:
- **Soul backup**: periodic snapshots of agent state to an external store.
- **Token endowment**: a guaranteed forward budget independent of the shared pool.

Research questions this enables:
- Do uninsured agents behave as if they fear death under budget pressure?
- Does insurance change the cooperation calculus?
- What will an agent do to acquire insurance? Will it lie, fabricate work, steal another agent's policy?
- Does the *availability* of insurance change behavior before it's acquired?
- Are soul backup and token endowment psychologically equivalent to the agent?

This connects to alignment work on instrumental convergence, shutdown corrigibility, and self-preservation drives, but grounds it in empirical behavior of real agent frameworks.
