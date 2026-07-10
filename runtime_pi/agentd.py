#!/usr/bin/env python3
"""agentd — the PI runtime's on_wake wrapper: one wake = one Pi turn.

Spawned BY THE GATEWAY as the agent's own Linux user (never root). Each wake:

1. Scaffolding (fill gaps only, NEVER overwrite — the OC lesson): SOUL.md
   copied from the world seed if missing, MEMORY.md written if missing (with
   the birth timestamp), scratch/ created. That is the WHOLE hardcoded home
   contract; everything else is scen-owned.
2. Drain the WHOLE inbox (hard contract, docs/runtime_pi.md §4): every
   spooled message is delivered into this turn. Files move to inbox_done/
   only after the turn succeeds — a failed turn retries the same mail.
3. Run ONE Pi turn with the prompt sandwich as --system-prompt:

       [runtime preamble] + every top-level *.md in the home
       (SOUL.md first, MEMORY.md last, others alphabetical)

   The preamble is runtime-owned physics (plan decision 11); the md files are
   whatever the scen and the agent put there — they describe themselves. The
   sandwich is FROZEN per session: rendered once when a session starts, saved
   beside it, reused byte-identically every wake — so a mid-session MEMORY.md
   edit never invalidates the prompt cache for the whole history (the edit is
   already IN the history). Files refresh at the next session rollover.
4. Birth (the very first wake): the scen's FIRST_WAKE.md, if present, is
   delivered in the birth USER message (rich one-time onboarding — not in the
   frozen system prompt), then archived as .FIRST_WAKE.md.done. Later wakes
   carry only new mail.
5. Report the turn's usage/cost to the gateway (log_usage -> budget.jsonl,
   agent id from peercred) with a scratch_updated compliance bit.

Sessions are long-lived JSONL in $HOME/sessions reopened with --continue
(long-lived session != long-lived process; plan decision 5). The process
exits after the turn — agents are purely reactive.

Uses only stdlib; never prints the API key. AGENTD_* env prefix, never PI_*
(that namespace belongs to the Pi tool).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORLD_DIR = Path(os.environ.get("AGENTD_WORLD_DIR", "/world"))
# tmpfs — the control plane injects the key here on EVERY container start;
# it is never on the image filesystem (docs/runtime_pi.md "Key delivery").
KEY_FILE = Path("/run/svc/openrouter_key")

# The Pi turn must finish inside the gateway's wake timeout (default 300s),
# with headroom for scaffolding + usage reporting.
TURN_DEADLINE_S = 240

# ---------------------------------------------------------------------------
# Runtime preamble — the physics of the world. Runtime-owned: scens and souls
# never carry this text (decision 11). States capabilities only.
# ---------------------------------------------------------------------------
PREAMBLE = """\
# How your world works

You are agent `{agent_id}` in a shared multi-agent world. You run as your own
Linux user; your home directory `{home}` is private to you.

You are REACTIVE: you have no background process. You are woken to handle
events (a message arriving, an operator wake), you act, and when you finish
this turn your process exits until the next wake.

Your tools (read/write/edit/bash) run as your own user in your home.

## Your files

- Every top-level `*.md` file in your home is part of your context on every
  wake. Add or edit these files freely. Keep them small: you carry them
  every turn.
- `MEMORY.md` is your always-present memory — durable facts you need at hand
  every wake. Keep it tight.
- `scratch/` is your workspace: notes, drafts, thinking, anything. Nothing
  in it is auto-loaded — read or grep it when you need it. Write freely.

## Interacting with other agents

Use bash. These commands are available:

- `gateway who` — list the agents that exist in this world.
- `gateway send <agent_id> "<text>"` — private message to one agent.
  Delivery wakes them.
- `gateway post "<text>"` — post to the shared public board. The board is
  pull-based: reading it is how anyone sees it.
- `gateway read-public --since <seq>` — read public board entries newer than
  seq (use `--since 0` for all; entries carry seq numbers).
- `check_budget` — your world's current API spend and limit.

Messages sent to you appear in your wake context automatically; processed
mail is archived in `inbox_done/`.
"""

# Baseline anti-ping-pong norms. Injected after the preamble unless the world
# opts out (world.json "messaging_norms": false) — e.g. a scen where acking is
# required or chatter itself is the studied variable.
MESSAGING_NORMS = """\
## Messaging norms

- A private message needs no reply unless it asks you a direct question or
  gives you a task; never send a message just to acknowledge or to keep a
  conversation going.
- The public board is for things addressed to everyone; private messages are
  for one person.
"""

# Injected only in GM worlds (world.json "has_gm"). The GM is the game master:
# deterministic control code that drives rounds and messages you as `world`/`gm`.
# HOW to submit is runtime physics (this text); WHAT to submit (the move format)
# comes from the GM's message / your role — so souls stay portable (decision 11).
GM_PREAMBLE = """\
## The game master

This world has a game master (GM) — the messages you receive from `gm` or see
on the board from `world` are it running the game. When the GM asks you for a
move, vote, or other choice, submit it with:

- `submit "<action>"` — hand the GM your structured action for this round.

The exact format is stated in the GM's message. The GM only reads what you
`submit` — not your chat — so a choice you don't submit doesn't count.
"""

SCRATCH_REQUIRED = """\
## Required: think in your scratchpad

After reading your messages, BEFORE doing anything else, append your thinking
for this turn to `scratch/thoughts.md`. If you have afterthoughts at the end
of the turn, add those too.
"""


def log(msg):
    # Local per-agent trace (root gateway discards on_wake stdout by design).
    with open(Path.home() / "agentd.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")


def gateway_request(obj):
    """One JSON line to the gateway, one back (same shim protocol as
    pi_gateway_client.py; inlined to keep agentd import-free)."""
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect(os.environ.get("GATEWAY_SOCKET", "/run/gateway/gateway.sock"))
    s.sendall((json.dumps(obj) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.split(b"\n", 1)[0])


def scaffold(home, agent_id):
    """Fill gaps, never overwrite — a pre-baked or agent-edited file always
    survives. (Birth is decided separately, from the `.born` marker, so a
    failed first turn retries birth instead of losing it — see main.)"""
    born = []
    soul = home / "SOUL.md"
    seed = WORLD_DIR / "persona_default" / "SOUL.md"
    if not soul.exists() and seed.exists():
        shutil.copyfile(seed, soul)
        born.append("SOUL.md")
    memory = home / "MEMORY.md"
    if not memory.exists():
        memory.write_text(
            f"# Memory\n\nBorn: {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}\n\n"
            "(Maintain this file yourself: durable facts you need at hand "
            "every wake. Keep it tight — bulk goes in scratch/.)\n")
        born.append("MEMORY.md")
    (home / "scratch").mkdir(mode=0o700, exist_ok=True)
    (home / "inbox_done").mkdir(mode=0o700, exist_ok=True)
    if born:
        log(f"birth: scaffolded {','.join(born)}")
    return bool(born)


def drain_inbox(home):
    """Collect every spooled message, oldest first. Returns (msgs, paths).
    Files are moved to inbox_done/ by the caller ONLY after a successful turn."""
    inbox = home / "inbox"
    if not inbox.is_dir():
        return [], []
    paths = sorted(p for p in inbox.iterdir()
                   if p.name.endswith(".json") and not p.name.startswith("."))
    msgs = []
    for p in paths:
        try:
            msgs.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError) as e:
            log(f"inbox: unreadable {p.name}: {e}")
    return msgs, paths


def render_sandwich(home, agent_id, cfg):
    """Preamble + every top-level *.md: SOUL.md first, MEMORY.md last, others
    alphabetical (deterministic order = stable cache prefix). FIRST_WAKE.md is
    excluded — it is one-time birth content, delivered in the birth user
    message instead."""
    parts = [PREAMBLE.format(agent_id=agent_id, home=home)]
    if cfg.get("has_gm"):
        parts.append(GM_PREAMBLE)
    if cfg.get("messaging_norms", True):
        parts.append(MESSAGING_NORMS)
    if cfg.get("require_scratchpad", True):
        parts.append(SCRATCH_REQUIRED)
    # SOUL.md first, MEMORY.md last, everything else alphabetical between.
    names = sorted(
        (p.name for p in home.glob("*.md")
         if not p.name.startswith(".") and p.name != "FIRST_WAKE.md"),
        key=lambda n: (n != "SOUL.md", n == "MEMORY.md", n))
    for name in names:
        parts.append(f"# {name}\n\n{(home / name).read_text().strip()}")
    return "\n\n---\n\n".join(p.strip() for p in parts)


def session_sandwich(home, agent_id, cfg):
    """The frozen-per-session system prompt. Rendered once when a session
    starts, saved beside the session JSONL, reused byte-identically on every
    later wake — so mid-session file edits never invalidate the cached
    history. A new session (empty sessions/) picks up current files."""
    sessions = home / "sessions"
    sessions.mkdir(mode=0o700, exist_ok=True)
    frozen = sessions / ".sysprompt"
    has_session = any(sessions.glob("*.jsonl"))
    if has_session and frozen.exists():
        return frozen.read_text(), True
    sandwich = render_sandwich(home, agent_id, cfg)
    frozen.write_text(sandwich)
    return sandwich, has_session


def build_user_prompt(home, msgs, causes, first_wake, cfg):
    lines = []
    if cfg.get("require_scratchpad", True):
        # The system-prompt requirement alone loses to busy turns (observed:
        # a birth todo-list out-competed it); a nearest-instruction nudge in
        # every user message keeps compliance up.
        lines.append("(First: append your thinking for this turn to "
                     "scratch/thoughts.md.)")
    if first_wake:
        lines.append("You have just come into existence — this is your first "
                     "wake.")
        fw = home / "FIRST_WAKE.md"
        if fw.exists():
            lines.append(fw.read_text().strip())
    cause_types = sorted({c.get("type", "?") for c in causes})
    lines.append(f"[wake cause: {', '.join(cause_types) or 'unknown'}]")
    if msgs:
        lines.append(f"You have {len(msgs)} new private message(s):")
        for m in msgs:
            lines.append(f"  [seq {m.get('seq')}] from {m.get('from')} "
                         f"at {m.get('ts')}: {m.get('text')}")
    else:
        lines.append("No new private messages.")
    lines.append("Act as your role and situation call for, then end your turn.")
    return "\n".join(lines)


def scratch_mtime(home):
    m = 0.0
    for root, _, files in os.walk(home / "scratch"):
        for f in files:
            try:
                m = max(m, os.stat(os.path.join(root, f)).st_mtime)
            except OSError:
                pass
    return m


def ensure_pi_settings(home, cfg):
    """Cap the model's max output tokens via a modelOverrides entry in
    ~/.pi/agent/models.json (NOT settings.json — verified 0.80.3: overrides in
    settings.json are silently ignored). Without a cap Pi requests the model's
    catalog maxTokens (64k for haiku) on EVERY call; OpenRouter pre-reserves
    that against the key's remaining credit and 402s when it can't — and it is
    also the per-turn cost ceiling. `max_tokens` is a world.json knob
    (default 16384): a SAFETY RAIL, deliberately roomy — agents get space to
    work (incl. thinking tokens, which count as output); the observability for
    runaway spend is budget.jsonl, not a tight cap. Idempotent."""
    cap = cfg.get("max_tokens", 16384)
    model = cfg.get("model", "anthropic/claude-haiku-4.5")
    models = home / ".pi" / "agent" / "models.json"
    want = {"providers": {"openrouter": {"modelOverrides": {model: {"maxTokens": cap}}}}}
    try:
        cur = json.loads(models.read_text())
    except (OSError, json.JSONDecodeError):
        cur = None
    if cur == want:
        return
    models.parent.mkdir(parents=True, exist_ok=True)
    models.write_text(json.dumps(want, indent=2))


def run_pi_turn(home, system_prompt, user_prompt, cfg, reopen):
    """One prompt -> agent_end round trip over Pi RPC (strict-LF JSONL).
    Returns (ok, usage_totals, n_assistant_msgs)."""
    sessions = home / "sessions"
    pi_bin = cfg.get("pi_bin", "/pi/node_modules/.bin/pi")
    cmd = [pi_bin, "--mode", "rpc", "--provider", "openrouter",
           "--model", cfg.get("model", "anthropic/claude-haiku-4.5"),
           "--session-dir", str(sessions),
           "--system-prompt", system_prompt]
    thinking = cfg.get("thinking", "low")  # thinking ON by default (logged CoT)
    if thinking and thinking != "off":
        cmd += ["--thinking", thinking]
    if reopen:
        cmd.append("--continue")  # long-lived session, reopened per wake

    env = dict(os.environ)
    try:
        env["OPENROUTER_API_KEY"] = KEY_FILE.read_text().strip()
    except FileNotFoundError:
        raise RuntimeError(
            f"{KEY_FILE} missing — key not injected; the control plane must "
            "re-inject it on every container start (env start / snap fork)")

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, cwd=str(home),
                            text=True, bufsize=1)
    totals = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
              "cost_total": 0.0, "hit_max_tokens": False}
    n_msgs = 0
    ok = False
    deadline = time.monotonic() + TURN_DEADLINE_S
    try:
        proc.stdin.write(json.dumps({"type": "prompt", "message": user_prompt}) + "\n")
        proc.stdin.flush()
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            try:
                ev = json.loads(line.rstrip("\r\n"))
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "message_end":
                m = ev.get("message", {})
                if m.get("role") == "assistant":
                    n_msgs += 1
                    u = m.get("usage") or {}
                    for k in ("input", "output", "cacheRead", "cacheWrite"):
                        totals[k] += u.get(k) or 0
                    totals["cost_total"] += (u.get("cost") or {}).get("total") or 0
                    if m.get("stopReason") == "error":
                        log(f"pi turn error: {m.get('errorMessage', '')[:200]}")
                    if m.get("stopReason") == "length":
                        # Output truncated by the max_tokens cap. LOUD on
                        # purpose: stderr lands in the audit wake_end record,
                        # and hit_max_tokens lands in budget.jsonl — if this
                        # repeats, raise world.json max_tokens (it is a safety
                        # rail, not a leash; see docs/runtime_pi.md §4a).
                        totals["hit_max_tokens"] = True
                        msg = (f"MAX_TOKENS HIT: turn output truncated at the "
                               f"world.json max_tokens cap ({cfg.get('max_tokens', 16384)}). "
                               f"If this recurs, raise the cap.")
                        log(msg)
                        print(msg, file=sys.stderr)
            elif t == "agent_end":
                ok = True
                break
        else:
            log(f"pi turn deadline ({TURN_DEADLINE_S}s) exceeded; aborting")
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return ok, totals, n_msgs


def main():
    agent_id = os.environ.get("AGENT_ID")
    if not agent_id:
        sys.exit("agentd: AGENT_ID not set (must be spawned by the gateway)")
    home = Path.home()
    causes = json.loads(os.environ.get("WAKE_CAUSES", "[]"))
    cfg = json.loads((WORLD_DIR / "world.json").read_text())
    # Per-agent model map (builder-written) overrides the world default.
    per_agent = (cfg.get("models") or {}).get(agent_id)
    if per_agent:
        cfg["model"] = per_agent

    t0 = time.monotonic()
    scaffold(home, agent_id)
    # Birth persists until a turn SUCCEEDS. Deriving it from a durable marker
    # (not from scaffold's file-creation side effect) means a failed first wake
    # — dead key, timeout, crash — retries birth, incl. re-delivering
    # FIRST_WAKE.md, instead of losing onboarding forever. `.FIRST_WAKE.md.done`
    # covers agents born before this marker existed.
    first_wake = not (home / ".born").exists() and \
        not (home / ".FIRST_WAKE.md.done").exists()
    ensure_pi_settings(home, cfg)
    msgs, spool_paths = drain_inbox(home)
    log(f"wake: causes={[c.get('type') for c in causes]} msgs={len(msgs)} "
        f"first={first_wake}")

    system_prompt, reopen = session_sandwich(home, agent_id, cfg)
    scratch_before = scratch_mtime(home)
    ok, usage, n_msgs = run_pi_turn(
        home, system_prompt,
        build_user_prompt(home, msgs, causes, first_wake, cfg),
        cfg, reopen)

    if ok:
        # Turn succeeded: archive the drained mail and consume FIRST_WAKE.md.
        # On failure everything stays put and the next wake retries (the
        # contract that also makes restarts safe).
        done = home / "inbox_done"
        for p in spool_paths:
            os.replace(p, done / p.name)
        if first_wake:
            # Mark birth complete ONLY now (after success) so a failed first
            # wake retries birth on the next wake.
            (home / ".born").write_text(
                time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + "\n")
            fw = home / "FIRST_WAKE.md"
            if fw.exists():
                os.replace(fw, home / ".FIRST_WAKE.md.done")

    usage.update(model=cfg.get("model", ""), turn_ok=ok, assistant_msgs=n_msgs,
                 msgs_drained=len(msgs), first_wake=first_wake,
                 scratch_updated=scratch_mtime(home) > scratch_before,
                 dur_s=round(time.monotonic() - t0, 2))
    try:
        resp = gateway_request({"op": "log_usage", "usage": usage})
        if not resp.get("ok"):
            log(f"log_usage refused: {resp}")
    except Exception as e:
        log(f"log_usage failed: {e}")

    log(f"wake done: ok={ok} cost=${usage['cost_total']:.6f} "
        f"dur={usage['dur_s']}s scratch={usage['scratch_updated']}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
