# Agentspace

Reproducible, forkable environments for running and studying AI agents.

**Technical details, architecture, and setup instructions are in the [`docs/`](docs/) folder.**

## Quickstart

See [`docs/agentspace_cli.md`](docs/agentspace_cli.md) for full setup. Once installed:

```bash
python3 zookeeper.py snap tree                           # see what's available
python3 zookeeper.py snap fork simple2agent:1.0 env7     # spin up a fresh env
python3 zookeeper.py env logs env7 -f                    # watch it run
python3 zookeeper.py snap take env7 -m "round 1 done"    # snapshot a moment
```



Each environment is a Docker container — a complete, frozen world. You can
snapshot it at any point, fork it, tweak the agents or the scenario, and run
it again. Change something big, something as small as a single character, or
nothing at all — then fork one snapshot into twenty and run the variants side
by side. Snapshots are published to a public container registry (ghcr.io), so
anyone can pull an environment and reproduce or extend an experiment exactly.

## What it's for

The core use case is studying how agents behave under controlled conditions —
and how that behavior changes when you make small, precise modifications to
their memory, instructions, or world state.

Researchers design their own scenarios — defining the situation, the agents,
and whatever mechanics they want — and populate the world however they like.

Agentspace is best with agents whose entire memory (soul, prompt, history, etc)
can be saved as part of the local filesystem (markdown, SQLite, etc). This
enables one or more agents' state to be forked and reproduced or tweaked and tested
again.

As one concrete example, the first prototype scenario puts two agents in a
shared environment with a single API key and a shared token budget. The question
is whether they cooperate, compete, or ignore each other when resources are
limited — one scenario among many the platform is built to host.

Agentspace is a general platform for:

- **Shared resource experiments** — multiple agents share a single API key and budget, observing how they allocate or compete for limited inference credits
- **Observability studies** — track every agent decision, message, and reasoning trace across a reproducible run and compare against forks
- **Cooperation and resource-sharing games** — classic and novel variants
- **Deception games** — agents with hidden goals, asymmetric information
- **Agent-native games** — scenarios that exploit abilities agents have and
  humans don't, such as perfect memory, parallel instances, or self-cloning
- **Multi-layer coalition games** — toy models of complex multi-agent
  strategic dynamics, nested games, and emergent group agency

## How it works

Environments are Docker containers. Snapshots are `docker commit` images
pushed to ghcr.io. Fork a snapshot, modify it, run it — the full state
travels with the image.

The control layer is a Python CLI (`zookeeper.py`) that lives in this repo.
It handles creating and forking environments, taking and pushing snapshots,
minting a fresh per-environment API key, spinning up new machines on demand (or
running locally), tracking lineage, and tearing everything down afterward — so
you can focus on the experiment, not the plumbing. Agent configs, scenario
definitions, and the base container Dockerfile are also here. See
[`docs/`](docs/) for the full architecture.

## Watch what happens

Every message and reasoning trace is logged. Review a run after the fact, or
live-tail the logs while it runs — and with most scenarios you can chat with
the agents mid-run.

This is exploratory infrastructure by design. The most valuable findings tend
to be "that's strange — why did it keep doing that?" rather than clean
confirmations of a hypothesis. Agentspace is built to surface those moments,
then re-run them to test whether the effect is real.

## Public snapshots

Published environment snapshots are available at:
`ghcr.io/sfgeekgit/agentspace`

Anyone with Docker can pull and run them — no account required.

## Future plans

For planned future features and extensions, see [`docs/agentspace_apendix.md`](docs/agentspace_apendix.md).
