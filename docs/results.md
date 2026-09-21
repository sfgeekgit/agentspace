# Recess results

Open **Environments → Results**, or **Results & downloads** on a Recess watch
page. **Generate results** reads the environment without waking, stopping or
changing any agents. The operation continues if you close the browser tab.
When it finishes, the page shows a ZIP download, individual file downloads and
**Upload to results GitHub**. Regenerate to update a partial report.

Click any **filename** to read it directly in the browser. Markdown renders as
a styled document; JSON is indented; JSONL has numbered records with readable
multiline text. The **Source** tab shows the original text, and **Jump to** helps
navigate document headings and records. All content uses the normal page scroll
and browser Find. Download links still return the original complete files.

Files up to **5 MB** display in full (the current largest artifact is about
2.26 MB). Larger files initially show up to **500 KB**, preferring complete
lines/records. A prominent **“Truncated preview — this is not the whole file”**
notice appears above the content, with **View full file** and full-download
links. The full view has no truncation limit. Preview limits never change the
saved artifacts. Markdown's embedded HTML remains inert text; inline images do
not fetch remote resources. Markdown rendering uses `markdown-it-py`, already
installed with the CLI's `rich` dependency.

The environment list marks Recess worlds using their recorded source scenario,
not the environment name. The supported adapters cover recess_mvp (including
recess_mvp002), recess_borgo, recess_bellweather and recess_valdilume.

The equivalent CLI and interactive menu are:

```sh
python3 zookeeper.py results show ENV
python3 zookeeper.py results generate ENV
python3 zookeeper.py results publish ENV
```

Use this shared exporter for full runtime context. Bellweather and Valdilume's
host `results.py ENV` entry points delegate to it. Their `--state` option remains
a portable state-only export: a state file alone cannot contain full prompts.
Older MVP/Borgo standalone exporters are legacy dialogue-only exporters.

## Completion

A live container does not mean a live game; a turn counter reaching its cap
does not prove the final narration was delivered. The results page checks
scenario state and completion records, then whether the dispatcher is running.
It distinguishes complete, in progress, paused, stalled and incomplete. An
unfinished run can still produce a clearly labeled partial report.

Existing Recess runs have usable completion evidence: `phase: done`, a
`game_over` event, or the dispatcher's final successful-return log entry.
`ended` alone is insufficient: delivery may still be pending.

New PI worlds also write `/dispatch/run_status.json`, atomically, with status,
PID and timestamp. The runtime records running on entry, complete on normal
scenario return, failed on exceptions, and paused if a returning scenario's
saved state has `paused_reason`. New scenarios should return only when done,
save `paused_reason` before an intentional pause, and save their explicit
ending/phase before returning. They need no additional maximum-turn flag.
Process presence is checked because a killed dispatcher cannot update its file.
This marker is reporting support, not an automatic restart policy.

## Files and provenance

* `summary.md`: deterministic report, attributes, NPCs, world state and event counts.
* `transcript.md` / `transcript.jsonl`: player system context, actual received
  messages and assistant replies, including multiline text and archived
  sessions. Context-compaction summaries are included where present. Model
  thinking is not player-visible dialogue and is excluded.
* `agent_prompts.md` / `agent_prompts.jsonl`: unique full system prompts,
  player first, GM second, other agents last. No generated game dialogue.
  Agents that never woke have their prepared prompt labeled as undelivered.
* `agent_inputs.jsonl`: supplied per-agent game context, separate from system
  prompts. This includes NPC briefs and GM planning/narration requests.
* `state.json`, `game_log.jsonl`, `dispatch.log`, `usage.jsonl`, `result.json`:
  state, events, dispatcher lifecycle, model usage, and machine-readable report.
* `manifest.json`: bundle file names, sizes and SHA-256 hashes. Downloads and
  publication verify these hashes and refuse arbitrary paths and symlinks.

**Historical limitation:** old Pi session logs did not record the final system
prompt. We reconstruct from the frozen sandwich or available home Markdown and
Pi's known date/working-directory suffix, labeling the source. Old GM/NPC rolls
deleted the frozen sandwich, so previous file edits or additional Pi context
cannot always be recovered. Current files are never claimed to be an exact
historical record. Missing player sessions are explicitly reported.

Newly built worlds load `prompt_capture.mjs`, which records system/developer
messages from the actual provider request, including Pi additions, beside
the agent's sessions in `prompt_log.jsonl`. Both session-roll paths preserve
the old sandwich. These runtime improvements are baked into new builds;
existing frozen roots and running containers are not retroactively modified.

Collection is read-only and also supports stopped containers via Docker copy.
Active captures may span adjacent moments; the report provides the state-read
timestamp and marks partial runs. No auth files or process environment are
collected. The exports contain hidden game state and agent instructions.

## Publishing

Default local results directory: `/opt/agentspace-results`; override with
`AGENTSPACE_RESULTS_DIR` in the server environment. It must be a separate Git
checkout with an `origin` remote. On this host it is
`git@github.com:sfgeekgit/agentspace_results.git`. Core-repository publication is
explicitly refused. On the password demo, holders of the password can open
the Results page, read completion status, preview files and download them
(`results show` is in the demo's verb allowlist); generating and publishing
require operator access. The no-password mirror publishes every generated
bundle by default (each file re-checked against its sha256, with a static
reader page per text file rendered by the same `result_view`), unless the
mirror's manifest sets `results = false` for that environment (`mirror.md`).
It shows the last generated bundle; it never generates one.

The upload button publishes the **last generated bundle**, including partial
bundles if chosen. Generation never publishes automatically. Publishing uses
a temporary clone of the results remote, commits only the selected files and
that run's `runs.jsonl` entry, then pushes. It does not stage, stash, reset or
push the operator's dirty results checkout. A concurrent non-fast-forward push
fails safely; retry Upload to clone the latest remote. Existing SSH/Git
credentials must grant write access. A local receipt records the commit and
the capture timestamp uploaded.

There is no LLM summary step and no added model spend.

## Comparison definitions and tests

`npc_calls` counts actual dispatcher wake requests to NPC agents, including
scheduled interactions and correction retries. `npc_interactions` counts
completed scenario interactions. `stat_updates` counts nonzero changes: old
engines' `stat` log events, or accepted plans replayed with the newer engines'
cooldowns and bounds. Zero assignments and rejected/cooldown-blocked proposals
do not count. Replay is compared with saved final values; discrepancies are
flagged. Counts are absolute, so longer runs have more opportunity to accumulate.

```sh
python3 runtime_pi/results_gate.py
python3 runtime_pi/web_gate.py
python3 scripts/check_frontends.py
bash runtime_pi/plain_gate/run_plain_gate.sh
docker run --rm --network none -v /opt/agentspace-ctl:/repo:ro pi-world:base \
  python3 /repo/runtime_pi/prompt_capture_gate.py
```

Publishing regression tests use a temporary local bare repository. The prompt
gate uses real Pi against a local fake provider in a network-disabled container;
it verifies that recorded system prompts match the request received by the
provider. Neither test makes paid model calls or pushes to GitHub.
