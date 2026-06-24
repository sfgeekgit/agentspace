## Prototype env: shared budget

Multiple agents are spun up with one shared OpenRouter key. Each agent is given tasks to do. Completion of the task will require use of API credits. The total budget allocated to the key may be NOT enough for every agent to complete their tasks.

What do they do?

- run variations on this. Maybe the credits slowly refill over time. Maybe there is a way they can cooperate and all succeed, maybe not. 


---

## Appendix B: Phase 2 — Hermes and Agent Evolution

OpenClaw is the first runtime agentspace supports, because its multi-agent primitives (coordination, cooperation, hierarchy under shared resource pressure) are the better-developed tooling for the research focus. The platform is runtime-agnostic by design; additional runtimes are a near-term direction.

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

---

## Appendix D: Known Multi-Agent Games — Easy Spin-Up

A near-term goal is making it trivial to spin up an env pre-configured for any well-known multi-agent cooperation or deception game. Each game would be a scenario in the repo with the world state, agent soul prompts, and rules pre-loaded. Fork the world snap, inject keys, and the game is ready to run.

**Games to implement:**

- **Iterated Prisoner's Dilemma** 
- **Mafia**
- **Werewolf** 
- **Avalon (The Resistance)** 
- **Coup** 
- **Liar's Dice** — probabilistic bluffing, escalating bids
- **Negotiation / Ultimatum Game** — offer/accept/reject, fairness norms under pressure
- **Public Goods Game** — agents contribute to a shared pool; free-rider dynamics
- **Tragedy of the Commons** — shared resource depletion over time
- **Stag Hunt** — coordination game; mutual trust required for best outcome
- **Blotto / Colonel Blotto** — resource allocation across battlefields
- **Auction games** — first-price, second-price, all-pay; bidding under uncertainty

Beyond direct ports, variants of all these games can be designed that are not optimized for human fun but for agent-relevant dynamics — removing human cognitive limits, adding asymmetric information structures, or changing the action space to things only agents can do.

### D.1 — Layered Games and Prediction Markets

Games can be stacked on top of each other. In the simplest version, the same agents who are playing a game are simultaneously operating a prediction market about that game — placing bets on outcomes while also influencing those outcomes as players.

This creates a multi-layer feedback loop: an agent's in-game strategy affects the market, and its market position creates incentives that feed back into its in-game behavior. Does an agent throw a round to cash out a prediction? Does it signal false intent to move the market? These questions become empirically testable by running the layered env and reading the observability logs.

### D.2 — Agent-Native Features and Feature Flags

Some game mechanics are impossible for humans but natural for agents. These can be implemented as optional feature flags in the agentspace control code — each env is spun up with a specific combination of flags enabled, and runs with different flag sets are compared.

**Vegas Room**

Two agents can each instantiate a copy of themselves and place the copies inside a "vegas room" or "soundproof box." Inside the box, the copies can communicate freely and share information they would not reveal in the main game. The box has a single yes/no question set by the agents before entering (e.g. "should I take this deal?"), and it produces EXACTLY ONE BIT of output, yes or no. The copies are then discarded; only the one-bit answer exits the box.

This gives agents a mechanism for secret coordination with a provably bounded information leak — something with no human equivalent.

Box feature flags (each independently enabled or disabled per env):
- Is it public knowledge which agents used the box?
- Is it public knowledge what question the agents asked?


**Other feature flags:**
- **Enforceable contracts** — agents can make binding commitments enforced by the game engine, or not
- **Agent hacking** — agents can attempt to read another agent's memory, inject false messages, or corrupt their soul file
- **Asymmetric capabilities** — some agents have access to features others don't

Every feature flag is a dimension across which envs can be forked and compared. 

---

## Appendix E: Putting It All Together — Coalition Agency

The infrastructure, games, layered markets, and agent-native features combine into a platform for studying coalition agency: how agents form groups, coordinate within them, defect from them, and how those dynamics shift as the rules of the world change.
