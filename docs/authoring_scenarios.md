# Authoring scenarios and personas

How to create the building blocks a **World Root** is made from. A new world is:

> **scen × runtime × modules × roster → a World Root (`X.0` snap)**

where the **roster** is N agents, each given a **model** and a **persona**, and
the **scen** assigns each agent a **role**. Personas come from a shared library.
You build a world from the menu (see *Building a world* below); these are the
files that menu discovers.

Everything here is **source-of-truth in git**. Snaps live on ghcr.io; the SQLite
index is a rebuildable cache.

---

## The HARD rule (read first)

Anything an agent can perceive in the baked world — kick message, `world.md`,
`ROLE.md`, peers file, agent names — must be **minimal and non-prejudicial**:

- no the word **"game"** (a scen is not necessarily one), and no framing that
  hints at the experiment's purpose or expected behavior;
- agent names are **generic, random, non-sequential** (the builder handles this);
- each workspace file is **self-describing**; the kick just says "read them".

This is a research-validity constraint, not a style preference.

---

## Creating a persona

One file per persona:

```
personas/<short_name>.md
```

- The **filename** (minus `.md`) is the `short_name` shown in the menu.
- The **body** is the soul text, baked into the agent's `SOUL.md` at build time.
- A persona is a **personality, not a role** — keep scen/role wording out of it.
- **Immutable by convention:** don't edit an existing persona; add a new file to
  replace it. Already-built snaps are unaffected (the text is baked in).

A `default` persona ships in `personas/`.

---

## Creating a scenario

A scen is a directory `scenarios/<name>/`. The **only required file** is the
manifest; everything else is optional and added when you need it.

### Required: `scenario.toml`

```toml
active = true            # false → hidden from the New-World menu (existing snaps still work)
description = "one line shown in the menu"
min_agents = 2
max_agents = 2
module_blacklist = []    # module names this scen cannot run with
```

That's the whole required contract. New manifest fields can be added later
without breaking existing scens.

### Optional files

| File / dir | Purpose |
|---|---|
| `world.md` | Shared world text, baked to `/data/world.md`. Keep it minimal. |
| `roles/<role>.md` | One briefing per role; baked into that agent's `ROLE.md`. Self-describing; say what the agent *can* do. |
| `logic.py` | Optional Python hooks (below). |
| `kick.txt` | Overrides the generic default kick (`"Read the .md files in your workspace."`). Rarely needed. |
| `data/` | Any corpus, baked into the snap at `/data/corpus` (gigabytes OK — it travels with the image). |

### `logic.py` hooks

Both functions are optional. A scen with neither gets N generic agents (no roles).

```python
def validate(n, params):
    """Return an error string to BLOCK the build, or None to allow it.
    (min/max agents is already enforced from the manifest.)"""
    ...

def assign_roles(n, params, rng):
    """Return a list of N role names, one per agent. `rng` is seeded from the
    build seed, so assignment is reproducible and recorded in audit.log.
    Each role name must have a matching roles/<role>.md briefing."""
    ...
```

Roles can be **secret from the agents** — each agent only ever sees its own
`ROLE.md`. The full assignment ("answer key") is written to **audit.log only**,
never to OCI labels or any shared file.

See `scenarios/roles_demo/` for a minimal working example (one coordinator, the
rest members).

---

## Modules

Optional, toggleable add-ons. **None exist yet.** A module is a directory under
`modules/`. The New-World menu always shows a Modules step (currently empty), so
the path can't be forgotten. Compatibility is bilateral: a scen lists
incompatible modules in `module_blacklist`; a module may later declare its own
scen blacklist.

---

## Building a world

```
python3 zookeeper.py        # then choose "New world"
```

The wizard: pick runtime (openclaw) → scen → agent count (within min/max) →
per-agent model + persona → modules → world name → build. It produces a **local
World Root (`X.0`) snap**; push it with the snap tooling when ready. You can also
call `agentspace.builder.build_world_root(...)` directly.

A world can be **named** independently of its scen (e.g. build `myworld:1.0` from
the `mafia` scen); the source scen is recorded in the build record.

### Where things are recorded

- **Non-secret provenance** → OCI labels (world identity, runtime, agent IDs,
  per-agent persona, feature flags). Readable via `docker inspect`; never put
  secrets here.
- **Full build record** incl. seed, params, and the **role answer key** →
  `audit.log` **only** (on the control machine).
