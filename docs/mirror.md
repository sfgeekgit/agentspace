# The public mirror

A no-password website for browsing this box: every environment (running,
dormant or stopped) with its facts, log views, replay in the browser, results
and downloads; every world root and snapshot; every scenario. It is a
**mirror**: a directory of static files that a publisher writes and Caddy
serves. No Python, docker, database or credential is reachable from the public
internet. The one thing a visitor can make the box do is "publish again", with
no arguments, at most once per minimum interval; and, for a finished recess
game whose results were never generated, "generate its results", once.

The rule is: **publish as much as possible that is not a security problem.**
Everything is public unless the operator's manifest says otherwise. That
includes environments launched from the password demo, which appear on the
mirror at the next refresh.

Live at `https://agentworldmaker.com/publicview/`. It is a path, not a
hostname, on purpose: a path appears in no certificate and no DNS record, and
the root of the domain stays behind the demo password. Nothing depends on the
path staying unknown.

| | Operator | Demo | Mirror |
|---|---|---|---|
| Address | ssh tunnel to 7788 | `agentworldmaker.com`, shared password | `/publicview/`, no password |
| What answers | the app | the app with the demo policy | Caddy `file_server` |
| Can do | everything | build, launch, run, chat, snapshot | read, replay, download, press Refresh |
| Sees | everything | everything | everything except what the manifest excludes, as of the last refresh |

## 1. Pieces

```
agentspace/mirror.py                       the publisher: cmd_publish(), cmd_show()
zookeeper.py                               `mirror publish [--manifest PATH]`, `mirror show`, the menu's Mirror branch
mirror/                                    the viewer: index, run, world and scenario pages, mirror.js, mirror.css; any other file here (a report page, say) is copied into every build
mirror_refresh.py                          the refresh trigger, 127.0.0.1:7787
deploy/agentspace-mirror-refresh.service   its unit (user cc, MemoryMax=300M)
mirror.toml.example                        a sample manifest
runtime_pi/mirror_gate.py                  the gate

/var/agentspace-ctl/mirror.toml            the manifest (operator-owned, like secrets.env; optional)
/srv/agentworldmaker-public/               the webroot: builds/<stamp>/ and a `current` symlink
```

`mirror publish` and `mirror show` are ordinary library verbs, so they have a
click command, a menu branch, and a generated form under Advanced tools. They
are not in `DEMO_VERBS`: the demo cannot change what the mirror excludes.

## 2. The manifest

Optional, and only exceptions live in it; see `mirror.toml.example`.

- `exclude = ["name", ...]` keeps environments off the mirror entirely (their
  snapshots stay in the library, without the link to them).
- `[env.<name>]` holds one environment's settings, all optional:
  `title`, `blurb`; `views` (exact world and scenario view names as `env
  watch` shows them; default every view except `raw`, which is whole audit
  records; an unknown name is reported and skipped); `agents` (default true);
  `thoughts` (default true: each agent's whole session plus its `thoughts`,
  `says`, `messages` and `scratchpad` facets; false: only what the agent
  said); `results` (default true); `budget` (default false: one OpenRouter
  lookup per publish so the board shows spend; without it the publisher makes
  no network call at all); `container` (publish only while the env name
  resolves to that container id prefix).
- An unknown setting or a wrong type is refused with the reason, so a typo
  cannot silently leave something public.
- Removing something from the mirror takes effect on the next publish; Caddy
  serves without caching. What someone already downloaded is gone for good.
- What is public by default is a lot: private messages between agents (in
  `feed`), hidden game state (the scenarios' spoiler logs, results bundles),
  every prompt, every agent's reasoning. None of it is a way into the box.
  The thing to keep in mind is an experiment whose agents have network access
  and could be told this URL.

## 3. What a publish does

**Environments.** For each one not excluded: one `docker inspect` (container
id, running, started). A running container gets **one** `docker exec`: a fixed
Python one-liner (`mirror.EXTRACTOR`) that tars exactly the files the views
read (the gateway logs, session files, scratchpads, `world.json`, and the
files of the scenario's declared views) to stdout, plus a marker saying whether
the gateway process is up. A stopped container gets `docker cp` of the same
places, since nothing can run in it. Nothing else ever touches the container,
and nothing the publisher does wakes, starts, stops or messages it.

On the host each tar is read in memory, regular files only, 25 MB per run,
four runs at a time; it is never unpacked to disk, so a symlink an agent left
in its scratch directory cannot point the publisher at a host file. The view
tree is rebuilt with `logwatch.tree` (what `env watch` and the web UI use,
minus docker), every line goes through the same parsers, and events are sorted
by time. Each published event is built field by field from the `Event`
dataclass (`ts`, `who`, `kind`, `text`), so a new private field in a log can
never leak. The environment's OpenRouter key, which is readable inside the
container, is replaced by `[redacted]` in every view and results file.

Work is only done for what changed. The extracted logs are hashed; a run whose
hash, container and settings match the previous build has its views
**hard-linked** from that build (builds are immutable, so links are safe and
cost no disk). A stopped container already published with the same settings
is final and is not read at all. A container that is gone, unreadable, over
the cap, or not the pinned one keeps its last published copy, marked `stale`
with the reason in its `coverage` line; if it was never published it is left
out. An env name that now resolves to a new container is simply the new run.

**Results.** If `/opt/agentspace-results/<env>` holds a generated bundle, its
files are copied, each re-checked against its sha256 as `results.download`
does, and every text file (`.md .txt .json .jsonl .log`, up to 5 MB) gets a
static reader page rendered by the app's own `result_view` (the demo's results
reader): the file is treated as untrusted there, HTML in it is escaped,
Markdown runs with raw HTML off and `javascript:` links refused, images are
not fetched. An unchanged bundle is hard-linked too. The mirror never
generates a bundle; `results generate` stays an operator action.

**The library.** `worlds.json` lists every world root and snapshot from the
db: reference, scenario, parent and root, creation message, runtime, model,
roster, flags, registry tag, notes, attachments, and the published
environments launched from it. It carries nothing from the env rows, where the
keys live. `scenarios/<name>.json` is written for every active scenario and
every scenario a published run came from: description, world briefing, role
prompts, README; all of it already public in the repository.

```
builds/<stamp>/
  index.html run.html world.html scenario.html mirror.js mirror.css web.css
  site.json                           the board and the library's summaries; written last
  worlds.json                         every world root and snapshot
  scenarios/<name>.json
  runs/<env>/run.json                 facts, lineage, roster, views, results
  runs/<env>/views/<slug>.jsonl       one event per line; <slug>.txt the same as plain text
  runs/<env>/results/<file>           the bundle; <file>.html its reader page
  runs/<env>/all.zip                  every view's .txt plus the results files
```

`site.json` is written last, then a temporary symlink is renamed over
`current`, so a reader never sees a partial build; if anything raises, the new
build directory is deleted and `current` is untouched. Builds beyond
`keep_builds` are pruned.

On this box (24 environments, 51 snapshots, 14 scenarios, seven results
bundles) a first publish takes about 13 seconds and 95 MB; a refresh with
little changed takes 3 to 4 seconds and under 1 MB, peaking near 125 MB of
memory.

## 4. The refresh trigger

`mirror_refresh.py`, a stdlib HTTP server on `127.0.0.1:7787`. It imports
nothing from `agentspace`, so it holds no secrets. It accepts exactly `POST
/refresh` with no query and no body (anything else is 404, a body is 400). If
the current build is younger than its `min_refresh_seconds` it answers
`{"as_of", "refreshed": false}`; otherwise it takes a file lock
(`/run/lock/agentspace-mirror`), runs `zookeeper.py mirror publish` with a
120 s timeout, and answers `{"as_of", "refreshed": true}`. Requests are served
one at a time, so a press during a publish waits and gets the same new build.
A failing publish answers `"error": "publish failed"` with the details in the
journal only, and is debounced like a successful one. So a flood of presses
costs one publish per interval, and a site nobody opens runs nothing.

**Generate results.** The one action beyond a refresh. Each recess
environment's `run.json` carries `game` (the completion `results show`
reports, judged on the host from the extracted dispatcher files: `state.json`,
`run_status.json`, `dispatchd.log`, and whether `dispatchd` is running) and
`results_status` (the published bundle's status, if any). When the game is
`complete` and the bundle is missing or was captured before the end, the
build marks the run `generate: true` and the run page shows a "Generate
results" button. It posts to `generate/<env>`, which Caddy proxies to the
trigger. The trigger accepts the name only if the *current build* marks that
env `generate` (anything else is 404; a body is 400), allows one attempt per
env per `min_refresh_seconds`, runs `zookeeper.py results generate <env>`
(a read of the container, five minutes max), then a publish. So nothing a
visitor sends is an argument the mirror did not write first, a game in
progress or one with current results cannot be captured, and the worst a
crowd can do is one unneeded capture of a finished game per interval. This is
the one deliberate exception to "a visitor can only read and refresh".

```
sudo cp deploy/agentspace-mirror-refresh.service /etc/systemd/system/
sudo systemctl enable --now agentspace-mirror-refresh
journalctl -u agentspace-mirror-refresh -f
```

## 5. The viewer

Plain HTML and one JavaScript file. Every URL is relative, so it works under
any prefix. It shares `web.css` with the app (copied into each build) and uses
the app's class names, so a restyle of the app restyles the mirror; the markup
is its own. Every piece of data reaches the page through `textContent`; the
gate greps for `innerHTML`. The site's Content-Security-Policy allows no
inline script.

- **index.html**: environments first (the board, live then dormant then
  stopped, with a filter box), then worlds and snapshots, then scenarios;
  "data as of" and Refresh.
- **run.html?id=<env>**: facts, the lineage strip (scenario → world root →
  snapshots → this environment, each linked), agents, a tab per view. *Latest*
  shows the last 500 events with "load earlier"; *Replay* plays the whole view
  paced by its own timestamps at 1× to 30×, with pause, seek and "skip gaps
  over N seconds". Filter, per-view `.txt` / `.jsonl` downloads, `all.zip`,
  and the results files, each with "read" (its reader page) and "download".
- **world.html?id=<snapshot id>**: a world root or snapshot: facts, the
  environments launched from it, the whole snapshot tree of its world, the
  roster, notes and attachments.
- **scenario.html?name=**: description, world briefing, role prompts, README,
  its environments and its worlds.

The reader pages under `results/` are the one place HTML is built from data,
and it is built by the publisher with the app's escaping renderer, not in the
browser.

## 6. Caddy

Inside the demo's site block, before the demo's own directives:

```caddyfile
	redir /publicview /publicview/ 308
	@mirror_refresh {
		method POST
		path /publicview/refresh
	}
	handle @mirror_refresh {
		request_body {
			max_size 0
		}
		rewrite * /refresh
		reverse_proxy 127.0.0.1:7787
	}
	@mirror_generate {
		method POST
		path /publicview/generate/*
	}
	handle @mirror_generate {
		uri strip_prefix /publicview
		reverse_proxy 127.0.0.1:7787
	}
	handle_path /publicview/* {
		root * /srv/agentworldmaker-public/current
		header Cache-Control "no-cache"
		header X-Robots-Tag "noindex, nofollow"
		@write not method GET HEAD
		respond @write 405
		file_server
	}
	@demo not path /publicview /publicview/*
	basic_auth @demo { ... }
```

The `@demo` matcher matters: `basic_auth` runs before `handle`, so without it
the password would cover the mirror too. There is no `robots.txt` entry,
because a `Disallow: /publicview/` line would advertise the path; the
`X-Robots-Tag` header and the site-wide `Referrer-Policy: no-referrer` do that
job instead. The webroot is owned by `cc`, directories 0755 and files 0644.

## 7. Security properties

- A visitor can read files, ask for a republish, and ask for the results of a
  finished game that has none. The only thing they send that reaches a
  command is that game's environment name, and only after the mirror itself
  published it as eligible.
- The publisher's inputs are the manifest, the db, the repo's scenarios, the
  results directory, and the containers' log files. Names that become file
  names or URL parameters (env names, snapshot ids, scenario names, results
  file names) are shape-checked first. Log content is only ever parsed as
  JSON lines and written back out as JSON or escaped HTML; nothing from a
  container is executed, unpacked or used as a path on the host.
- A demo password holder can put text on the public site (an environment
  name, a post, a note). That is accepted: the password goes to trusted
  people. What they cannot do through the mirror is reach anything: it adds no
  route into the app.
- The mirror carries no keys (env rows are never published; the env's key is
  redacted from logs and results), no host names, and container ids only as a
  12-character prefix.
- The cost of a hostile crowd is static file serving plus one publish per
  `min_refresh_seconds`. The trigger has its own memory cap; the app, the
  demo and the tunnel share nothing with it.
- Rollbacks: `mirror publish` with a corrected manifest; or remove the Caddy
  lines (restore `basic_auth` without the matcher) and reload.

## 8. Tests

`python3 runtime_pi/mirror_gate.py` (72 checks): the publisher and the
trigger against a fresh state directory, a fixture env row and a fixture log
tree in place of the container. No docker, no tokens, a few seconds. It covers
manifest validation, publish-by-default, view derivation against `logwatch`
event for event, the four-field event, key redaction, the library, per-env
settings and `exclude`, hard-linked reuse of unchanged runs, the atomic swap,
gone / unreadable / stopped / replaced / pinned containers, results hashes and
the escaped reader pages, pruning, game status and the `generate` flag, the trigger's debounce and
refusals for both routes, and the viewer's no-HTML rule. The web gate checks that the demo cannot run
`mirror publish`.
