# The public mirror

A no-password website that shows chosen environments from this box: their
facts, the log views the operator lists, replay of a run in the browser, and
downloads. It is a **mirror**: a directory of static files that a publisher
writes and Caddy serves. No Python, docker, database or credential is
reachable from the public internet. The one thing a visitor can make the box
do is "publish again", with no arguments, at most once per minimum interval.

Live at `https://agentworldmaker.com/publicview/`. It is a path, not a
hostname, on purpose: a path appears in no certificate and no DNS record, and
the root of the domain stays behind the demo password. Nothing depends on the
path staying unknown.

| | Operator | Demo | Mirror |
|---|---|---|---|
| Address | ssh tunnel to 7788 | `agentworldmaker.com`, shared password | `/publicview/`, no password |
| What answers | the app | the app with the demo policy | Caddy `file_server` |
| Can do | everything | build, launch, run, chat, snapshot | read, replay, download, press Refresh |
| Sees | everything | everything | what the manifest lists |

## 1. Pieces

```
agentspace/mirror.py                       the publisher: cmd_publish(), cmd_show()
zookeeper.py                               `mirror publish [--manifest PATH]`, `mirror show`, the menu's Mirror branch
mirror/                                    the viewer: index.html, run.html, scenario.html, mirror.js, mirror.css
mirror_refresh.py                          the refresh trigger, 127.0.0.1:7787
deploy/agentspace-mirror-refresh.service   its unit (user cc, MemoryMax=300M)
mirror.toml.example                        a sample manifest
runtime_pi/mirror_gate.py                  the gate

/var/agentspace-ctl/mirror.toml            the manifest (operator-owned, like secrets.env)
/srv/agentworldmaker-public/               the webroot: builds/<stamp>/ and a `current` symlink
```

`mirror publish` and `mirror show` are ordinary library verbs, so they have a
click command, a menu branch, and a generated form under Advanced tools. They
are not in `DEMO_VERBS`: publishing is the operator's decision.

## 2. The manifest

See `mirror.toml.example`. Rules:

- Only what is listed is published. `views` match the environment's world and
  scenario view names exactly (`env watch` shows them); an unknown name is
  reported and skipped, never guessed. Default: `feed`, `board`,
  `announcements`.
- `agents = true` adds one view per agent holding **what the agent said** and
  nothing else. `thoughts = true` upgrades that to the whole session
  (prompts, reasoning, tool calls) and adds the `thoughts`, `says`,
  `messages` and `scratchpad` facets. Note that `feed` already contains the
  text of private messages between agents; leave `feed` out to keep those off
  the mirror.
- `results = true` copies the bundle in `/opt/agentspace-results/<env>`, each
  file checked against its sha256 as `results.download` does. No bundle, or a
  file that fails its hash: the run is published without results.
- `budget = true` does one OpenRouter lookup per publish for the run's spend.
  Without it the mirror shows the limit only and the publisher makes no
  network call at all.
- **Name reuse.** A build records each run's container id. If the env name
  later resolves to a different container, that run is refused with a message
  and its last published copy stays, until you change the run's `id` or pin
  `container = "<id prefix>"`. A new run under an old name is never published
  by accident.
- Removing a `[[run]]` and publishing removes its files from the next build.
  What someone already downloaded is gone for good: publish only what you are
  willing to distribute.
- The scenario of every published run is published with it (description,
  world briefing, role prompts, from the scenario directory).

## 3. What a publish does

For each run: look up the env row, one `docker inspect` (container id,
running, started), then **one** `docker exec`: a fixed Python one-liner
(`mirror.EXTRACTOR`) that tars exactly the files the views read (the gateway
logs, session files, scratchpads, `world.json`, and the files of the
scenario's declared views) to stdout, plus a marker saying whether the gateway
process is up. Nothing else ever runs in the container, and nothing the
publisher does wakes, starts, stops or messages it.

On the host the tar is read in memory, regular files only, 50 MB per run; it
is never unpacked to disk, so a symlink an agent left in its scratch directory
cannot point the publisher at a host file. The view tree is rebuilt with
`logwatch.tree` (the same function `env watch` and the web UI use, minus
docker), every line goes through the same parsers, and events are sorted by
time. Each published event is built field by field from the `Event` dataclass
(`ts`, `who`, `kind`, `text`), so a new private field in a log can never leak.
The environment's OpenRouter key, which is readable inside the container, is
replaced by `[redacted]` wherever a log quotes it.

```
builds/<stamp>/
  index.html run.html scenario.html mirror.js mirror.css web.css
  site.json                      the board: title, generated_at, min_refresh_seconds, runs, scenarios
  runs/<id>/run.json             facts, lineage, roster, views, results
  runs/<id>/views/<slug>.jsonl   one event per line
  runs/<id>/views/<slug>.txt     the same as plain text
  runs/<id>/results/<file>       the bundle, if published
  runs/<id>/all.zip              every .txt plus the results
  scenarios/<name>.json
```

`site.json` is written last, then a temporary symlink is renamed over
`current`, so a reader never sees a partial build; if anything raises, the new
build directory is deleted and `current` is untouched. Builds beyond
`keep_builds` are pruned. A run whose container is missing, stopped, refused
or too large is **carried over** from the previous build and marked `stale`
with the reason in its `coverage` line; a run that was never published and
cannot be read is left out. A stopped environment therefore stays on the
mirror if it was published while it ran, and cannot be added afterwards
without starting it.

A publish of five runs takes about two seconds.

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

- **index.html**: the board, live runs first, then dormant, then stopped;
  the scenarios in play; "data as of" and Refresh.
- **run.html?id=**: facts, the lineage strip (scenario → world root →
  snapshots → this environment), agents, a tab per view. *Latest* shows the
  last 500 events with "load earlier"; *Replay* plays the whole view paced by
  its own timestamps at 1× to 30×, with pause, seek and "skip gaps over N
  seconds". Filter, per-view `.txt` / `.jsonl` downloads, `all.zip`, and a
  plain-text preview of results files.
- **scenario.html?name=**: description, world briefing, role prompts, runs.

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

- A visitor can read files and ask for a republish. Nothing they send is ever
  an argument to anything.
- The publisher's inputs are the manifest, which only the operator edits, and
  the containers it names.
- Disclosure is exactly the manifest. The mirror carries no keys, no host
  names, and container ids only as a 12-character prefix.
- The cost of a hostile crowd is static file serving plus one publish per
  `min_refresh_seconds`. The trigger has its own memory cap; the app, the
  demo and the tunnel share nothing with it.
- Rollbacks: `mirror publish` with a corrected manifest; or remove the Caddy
  lines (restore `basic_auth` without the matcher) and reload.

## 8. Tests

`python3 runtime_pi/mirror_gate.py`: the publisher and the trigger against a
fresh state directory, a fixture env row and a fixture log tree in place of
the container. No docker, no tokens, a few seconds. It covers manifest
validation, view derivation against `logwatch` event for event, the
four-field event, key redaction, the `thoughts` switch, the atomic swap, stale
carry-over, name reuse and pinning, results hashes, unpublish and pruning, the
trigger's debounce and refusals, and the viewer's no-HTML rule. The web gate
checks that the demo cannot run `mirror publish`.
