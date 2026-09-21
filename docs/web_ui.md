# The web UI

A browser front end over the same library the CLI and the menu use. Pages are
rendered by Python (`web.py` routes, `web_views.py` templates), verbs run as
`zookeeper.py` child processes, and the JavaScript (`web.js`) only wires
buttons, streams output, and draws the watch page. No framework, no build
step, stdlib HTTP server. It binds `127.0.0.1:7788` and runs as the
`agentspace-web` systemd service (`deploy/agentspace-web.service`).

Two ways in:

- **Operator:** `ssh -L 7788:127.0.0.1:7788 control-01`, then
  http://127.0.0.1:7788. Everything enabled, no password.
- **Public demo:** Caddy in front, on a public hostname with a shared
  password. Every request from there carries a header that switches the app
  to the demo policy (§5). See §6 for the deployment.

`AGENTSPACE_WEB_PORT` runs a second instance on another port, for a worktree
or a throwaway test copy.

There is a third, separate tier that is not this app at all: the **public
mirror** at `/publicview/` on the demo's hostname, no password. It is a
directory of static files written by `zookeeper.py mirror publish` from an
operator-owned manifest and served by Caddy's `file_server`; nothing a
visitor sends reaches this process. See [mirror.md](mirror.md).

## 1. Pages

| Page | Route | What it shows |
|---|---|---|
| Overview | `/` | intro, workflow strip, counts, recent environments, starting points |
| Scenarios | `/scenarios`, `/scenarios/<name>` | catalogue with search (inactive ones included); a scenario's briefing, role prompts, its world roots, a build button, its GitHub link |
| Worlds & snapshots | `/worlds`, `/worlds?view=snapshots`, `/worlds/<id>`, `/snapshots/<id>` | world roots as cards, or every snapshot; a root's environments and its snapshot tree; a snapshot's roster, notes, attachments, provenance, publish and launch |
| Environments | `/environments` | every env with its root, source snapshot and last recorded status |
| Watch | `/watch/<env>` | the observatory: agents, log views live or replay, budget, action buttons, chat, terminal instructions (§4) |
| Launch | `/fork/<id>` | `snap fork` as a page: name, budget, wake-or-wait, advanced overrides |
| Create a world | `/new` → `/new/<scen>` → `/new/<scen>/roster` → `POST /new/<scen>/build` | the menu's New-world wizard: scenario, settings and agent count, per-agent model and persona, modules, name, build |
| Advanced tools | `/tools` | a generated form for every remaining CLI verb, grouped; terminal-only verbs listed with their command; recent operations |
| Help | `/help` | the field guide |
| Results | `/results/<env>`, `/results/<env>/status`, `/results/<env>/files/<name>` | Recess environments only: generate a results bundle, read completion status, preview and download its files (`all.zip` for everything), publish to the results repository (`results.md`) |

Ids in URLs are snapshot ids (32 hex chars) and env names. Search boxes
filter the current page only.

## 2. How verbs run: the CLI bridge

`POST /run/<group>/<verb>` with form fields runs `python3 zookeeper.py <group>
<verb> …` as a child process and answers `{"id": …}`. `GET /runs/<id>` streams
the child's output and ends with `[exit N]`; the run outlives the browser tab,
and `GET /runs` lists recent ones. `POST /runs/<id>/stop` interrupts one.

The forms are generated from the click tree, so a new click command appears
in Advanced tools with nothing else done: an argument is a text box (nargs=-1
is one box, shlex-split server-side), a flag is a checkbox, a flag with an
off form is a select (default / on / off), a repeatable option is a textarea
(one per line). Argv is options, then `--`, then positionals, so positional
text that looks like an option is safe.

Every POST must carry the header `X-Agentspace: 1`. A cross-site HTML form
cannot add it, so this is the CSRF guard; the page's JavaScript always sends
it. Keep it: it also protects a browser that has cached a basic-auth
credential for the demo host.

`web.SPECIAL` names the only verbs written into `web.py`: `env watch`, `env
show` and `env chat` are pages rather than forms; `env exec`, `env enter` and
`scen env shell` are terminal-only, their POST and form routes answer 404,
and the pages print the command to run instead.

## 3. Create a world, then launch

The wizard mirrors the menu: `registry.scan_scens()` for the catalogue,
`builder.plan_roster()` for ids and roles (this imports the scenario's
`logic.py`, which is why scenario names are shape-checked before they reach
the filesystem), then `builder.cmd_build` in a child. The build prints
`UI_WORLD_ROOT:<snap_id>` on success and the page turns that into a "Launch
environment" link, so concurrent builds cannot be confused.

The launch page is `snap fork` with the menu's defaults: a world root
defaults to waking the agents, a saved snapshot defaults to waiting. On
success the page opens the environment's watch page.

## 4. The watch page

Header and agent cards come from the db rows. Then the JavaScript asks
`GET /views/<env>` for the view tree (`logwatch.view_tree`: world and
scenario views, then one node per agent with its facets) and streams the
selected view from `GET /stream/<env>/<view>`, live or, with `?replay=N`,
the whole run paced by its own timestamps (see `runtime_pi.md` §5b). Every
event is rendered as text, never as HTML.

`GET /info/<env>` is `env show` as JSON (docker probe plus OpenRouter);
`GET /budget/<env>` is the budget's used and limit. The header's buttons
follow the live status: Start when stopped, Wake/Stop/Logs/Roll sessions
when up, Sleep and Post when active, Take snap unless missing, Remove
always. Clicking an agent card opens its session; on an agent view the chat
box posts `POST /chat/<env>/<agent>`, which blocks for the turn (one turn per
agent at a time, 409 otherwise). Columns resize and remember their width.

## 5. The demo policy

The public demo is the same process, switched by one request header that
only the reverse proxy can set. `web.py`:

```python
DEMO_HEADER = "X-Agentspace-Public"   # Caddy sets it on every demo request; the tunnel never has it
DEMO_VERBS  = {...}                   # the verbs the demo may run (world build, snap fork, snap take, note,
                                      #   show, tree, list; env start/stop/sleep/kick/post/chat/logs/list/show;
                                      #   budget show; scen list)
DEMO_DROP   = {"attach", "souls", "host", "allow_key_leak"}   # fields that name server paths or other hosts
DEMO_MAX_BUDGET = 2.0                 # dollars per launch
DEMO_MAX_ENVS   = 15                  # live containers, all of them, before launches are refused
```

`demo_denied(verb, fields)` returns why the demo may not run a verb with
those form fields, or `None`. It is asked in two places, so the pages and
the enforcement cannot drift:

- **Every POST handler** (`/run/*`, `/chat/*`, `/new/*/build`) asks it before
  spawning anything and answers 403 with the reason. A button re-enabled in
  the browser inspector, or a hand-written request, gets the same refusal.
- **The pages** ask it to render: action buttons for refused verbs are
  disabled with a "needs the operator password" tag; Advanced tools shows
  every form, refused ones inside `<fieldset disabled>`; the launch page caps
  the budget input and disables the host and persona-file fields; the watch
  page gets `data-denied` on `<body>` so its buttons gray out too; and every
  page carries a notice that says it is a shared demo, explains the disabled
  controls, and links to the repo with an invitation to fork and self-host.

What stays operator-only, and why: anything that names a file or host on
the server (`snap attach`, `snap take --attach`, `snap extract`, `snap fork
--souls`, `--host`), the registry (`snap pull`, `snap push`), `env kill`
(no ownership yet, so nobody can delete the featured environments), `budget
topup`, `scen deactivate`, `scen env build`, and the terminal-only verbs.
A fork is also refused for a snapshot whose `feature_flags` request
`fs_isolation=sandbox`, because that mode mounts the docker socket. The
policy resolves the snapshot with the launcher's own resolver, so every
spelling of a reference (scenario:version, id prefix, short or full ghcr
tag) is checked, and a reference that does not resolve is refused rather
than passed on. The container cap counts `running` rows too (a fork records
its env as `running` until the status is next refreshed), and the budget
must be above zero.

Two checks that protect every front end, not just the demo, live in the
library: `env logs --agent` accepts only ids made of letters, digits,
underscore and hyphen, because the id is spliced into a shell line inside
the container; and persona names must be a plain stem that resolves inside
`personas/`.

Password holders can open the Results page: `results show` is in
`DEMO_VERBS` and the demo host's Caddy allowlist includes `/results/<env>`
with its `status`, `view/<file>` and `files/<file>` routes. `results
generate` and `results publish` stay operator-only (they write to the results
directory and push to a git repository).

A new verb is refused on the demo until it is added to `DEMO_VERBS`.

The web gate (`runtime_pi/web_gate.py`) covers the policy: refused verbs
return 403 and spawn nothing, capped forks pass, the pages render disabled
controls with the header and nothing different without it.

## 6. Deployment

**Service.** `deploy/agentspace-web.service`, user `cc`, loopback only,
`MemoryMax=900M` and `TasksMax=400` so a flood of viewers degrades the app
rather than the host. Install with
`sudo cp deploy/agentspace-web.service /etc/systemd/system/ && sudo systemctl enable --now agentspace-web`.

**State.** `/var/agentspace-ctl` is mode 0700 and `db.sqlite` and
`audit.log` are 0600: the db holds every environment's OpenRouter key in
plaintext. Everything that touches it runs as `cc`.

**Operator.** The ssh tunnel. There is no operator hostname and no operator
password; a request that reaches 7788 without the demo header is the
operator, and only Caddy and the tunnel can reach 7788.

**Public demo.** One Caddy site block: `basic_auth` with a bcrypt hash from
`caddy hash-password`, a `path_regexp` allowlist so only well-formed routes
reach the app, `reverse_proxy 127.0.0.1:7788` with `header_up
X-Agentspace-Public 1` (overwrites any client copy), `header_up
-Authorization`, and `flush_interval -1` so streams and run output are not
buffered. Plus a request body cap and the usual security headers. The exact
block is in the operator's deployment notes; the shape is:

```caddyfile
demo.example.com {
	basic_auth { demo <bcrypt hash> }
	request_body { max_size 1MB }
	@ok path_regexp ^/(|help|scenarios|worlds|environments|tools|new|models|runs|web\.css|web\.js)$|^/(scenarios|new)/[a-z0-9_]+(/(roster|build))?$|^/(worlds|snapshots|fork)/[0-9a-f]{32}$|^/(watch|views|info|budget)/[A-Za-z0-9_.-]+$|^/stream/[A-Za-z0-9_.-]+/[^/]+$|^/chat/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$|^/(run|form)/[a-z_]+(/[a-z_-]+){1,2}$|^/runs/[0-9a-f]{8}(/stop)?$|^/results/[A-Za-z0-9_.-]+(/(status|(files|view)/[A-Za-z0-9_.-]+))?$
	handle @ok {
		reverse_proxy 127.0.0.1:7788 {
			header_up X-Agentspace-Public 1
			header_up -Authorization
			flush_interval -1
		}
	}
	handle { respond "Not part of the demo." 403 }
}
```

Never proxy the app to the public without the header: without the demo
policy, `POST /run/*` is a shell on the box (several verbs take host paths,
and `cc` is in the docker group).

**What a leaked demo password means.** Whoever has it can spend the
OpenRouter balance up to the per-launch cap times the container cap, fill
the box with containers up to the cap, and sleep or stop environments. They
cannot read files, run code on the host, delete anything, or touch the
registry.

**Rollback.** Remove the site block and `sudo systemctl reload caddy`. The
tunnel keeps working.

## 7. Tests

- `python3 runtime_pi/web_gate.py`: starts the app against a temporary state
  directory with a fixture child in place of `zookeeper.py`; checks every
  verb has a form, argv rules, run lifetimes, watch, chat, wizard, models,
  the workspace pages, and the demo policy. About ten seconds.
- `python3 scripts/check_frontends.py`: every library `cmd_*` is wired into
  the CLI, the menu, and the web (a form or a `SPECIAL` entry).
- `scripts/check_web_workspace.cjs`: a Playwright browser pass over the pages
  with all writes intercepted. Needs `playwright` under `.preview-tools/node_modules`
  and `npx playwright install chromium`; run with
  `NODE_PATH=.preview-tools/node_modules node scripts/check_web_workspace.cjs`
  against a running instance (`AGENTSPACE_PREVIEW_URL`, default 7790).
- `scripts/check_results_reader.cjs` and `scripts/check_results_workspace.cjs`:
  the same kind of browser pass for the Results pages (read-only document
  checks, and one authorized regeneration with publication intercepted).
- `python3 runtime_pi/results_gate.py`: the results verbs against fixtures,
  no model calls. See `results.md`.

## 8. Adding a verb

Library `cmd_*` function, click command, menu branch, as in
`agentspace_cli.md`. The web form appears by itself. Decide whether the
demo may run it: add it to `DEMO_VERBS` if so, and to `DEMO_DROP` any of its
fields that name a server path or host. The gate fails if a click leaf has
neither a form nor a `SPECIAL` entry.
