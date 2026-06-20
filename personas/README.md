# Persona library

Each file `personas/<short_name>.md` is one **reusable persona**:

- the **filename** (minus `.md`) is the persona's `short_name`;
- the **file body** is the soul text baked into an agent's `SOUL.md` at
  world-build time.

Personas are runtime- and scen-agnostic, so the same persona can be tested
across many scens (e.g. one set of 10 personas × {Prisoner's Dilemma, Mafia}).

**Immutable by convention:** don't edit an existing persona — add a new file with
a new `short_name` to replace it. Git history is the audit trail. Already-built
snaps are unaffected by later edits, because the persona text is baked into the
snap at build time (and the `short_name` is recorded in an OCI label for
provenance).

Source of truth is these files in git. They are discovered by the persona
registry (`agentspace/registry.py`), not stored in SQLite.
