# GPT-6 workspace preview

Open **http://127.0.0.1:7790/**. The original UI remains at
http://127.0.0.1:7788/.

The preview lives in `/home/cc/agentspace-worktrees/gpt-6-7790` on branch
`ui/gpt-6-worlds-7790`. It is a separate server and checkout; it still uses the
existing Agentspace state directory and containers. Actions deliberately taken
through either interface affect the same experiments. Scenario file operations
apply to the checkout serving that interface.

If connecting over SSH, add `-L 7790:127.0.0.1:7790` to your tunnel alongside
the existing 7788 forwarding.

## User experience

- Overview: a short introduction, the workflow, inventory counts, recent
  environments, and starting points. No wall of command forms on first load.
- Scenarios: searchable catalog including inactive definitions; briefing/roles,
  related world roots, a build action, and a secondary GitHub source link.
- Worlds: individual world roots, their descendant snapshots and environments.
  Older scenario relationships are explicitly identified as inferred.
- Snapshots: roster, notes, attachments, provenance, publishing, and launching.
- Build: scenario settings → agent roster → local root → direct launch link.
  Completion carries the actual root ID, so concurrent builds are not confused.
- Launch: environment name, budget, wake choice, and optional advanced overrides.
  Completion opens the environment's observatory.
- Environments: a full-height watch page like the original UI, with persistent
  agent cards on the left, world/agent log tabs in the center, and budget plus
  action output on the right. Run controls are directly in the header. Columns
  resize with the mouse or keyboard and remember their widths. Live and replay
  views include every scenario-declared log and agent facet; private messaging
  sits below the stream. Minimized action output does not interrupt the operation.
  On narrow screens, agents form a horizontal strip and the budget stacks below.
- Advanced tools: the remaining CLI-backed operations, with generated forms.

`env exec`, `env enter`, and `scen env shell` are terminal-only. Their browser
POST endpoints and form endpoints are rejected. An environment's right sidebar
provides copyable CLI instructions. This is the intentional parity exception
requested for the redesign; existing command behavior stays in the libraries.

New-user notes are in:
`/home/cc/2026-09-14-gpt-6-agentspace-first-steps-7790.md`.

## Running this preview

A dedicated user service is installed and enabled:

```bash
systemctl --user status agentspace-gpt6-7790
systemctl --user restart agentspace-gpt6-7790
journalctl --user -u agentspace-gpt6-7790
```

Its unit is `deploy/agentspace-gpt6-7790.service`. It never controls the original
`agentspace-web` service. For a manual run after stopping only this preview:

```bash
cd /home/cc/agentspace-worktrees/gpt-6-7790
AGENTSPACE_WEB_PORT=7790 python3 web.py
```

## Validation

```bash
python3 scripts/check_frontends.py
python3 runtime_pi/web_gate.py
NODE_PATH=.preview-tools/node_modules node scripts/check_web_workspace.cjs
```

The web gate uses a temporary state directory and fixture environments. The
browser check intercepts every write and uses synthetic observation streams,
covering scenario search, source links, the build/launch journey, notes, private
chat, replay, terminal guidance, and 13 routes at mobile width. Playwright is
installed locally under the ignored `.preview-tools/` directory; no runtime
frontend dependency or build step was added.

The live preview was also checked by reading an existing environment's view
catalog, status, budget, and log events. No real experiments were built, launched,
posted to, stopped, or otherwise altered during validation. New paid builds and
registry pushes were not exercised against the live installation.
