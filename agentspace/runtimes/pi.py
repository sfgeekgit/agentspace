"""PI runtime: Pi-brained agents as Linux users behind the pi_gateway.

Same module surface as runtimes/openclaw.py; env.py/snap.py/builder.py
dispatch on the snap's `runtime` OCI label. Runtime internals (protocol, wake
contract, agentd) are documented in docs/runtime_pi.md — this module only
translates zookeeper verbs into them.

No sandbox-sibling machinery here: isolation is kernel permission bits inside
ONE container, so the fs_isolation flag (and all its host-mount mechanics) is
never set for PI worlds and `docker commit` is always the complete snapshot.
"""

import json
import shlex
import shutil
import tempfile
import time
from pathlib import Path

from .. import docker_host

NAME = "pi"
MENU_NAME = "PI"

BASE_IMAGE = "pi-world:base"          # agentspace:base + node + Pi (EXACT pin; formerly pi-world:step2)
# Present in any image carrying this runtime (Pi's npm dir); the builder's hard
# compatibility check for source images.
RUNTIME_MARKER = "/pi"
SUPPORTS_GM = True                    # gmd/gmlib adapter exists (GM scens are PI-only today)
# Canonical container config stamped onto every committed world root
# (docker commit --change): normalizes away whatever USER/ENTRYPOINT/CMD/
# WORKDIR a source image carries — and the builder's own assembly hardening —
# so `docker run -d <root>` always behaves the way the control plane expects.
# GOTCHA (verified): `ENTRYPOINT []` is a SILENT NO-OP in commit --change
# (`CMD []` does clear) — hence the run command lives in ENTRYPOINT here.
COMMIT_CHANGES = ("USER root", "WORKDIR /data",
                  'ENTRYPOINT ["sleep", "infinity"]', "CMD []")
GATEWAY_LOG_PATH = "/var/log/gateway.log"
SOCKET_PATH = "/run/gateway/gateway.sock"
KICK_FILE_PATH = "/world/kick.txt"
CLIENT = "python3 /runtime_pi/pi_gateway_client.py"

# PI isolation is intrinsic — no flags needed for parity with anything.
DEFAULT_FEATURE_FLAGS: dict = {}

# Empty kick = wake with no message (agents drain their inbox / run FIRST_WAKE).
DEFAULT_KICK = ""

# Source of the runtime files baked into every world image.
RUNTIME_SRC = Path(__file__).resolve().parents[2] / "runtime_pi"
# gmd.py is the GM launcher/adapter; gmlib.py (the runtime-neutral GM library)
# is copied from agentspace/ so the in-container scen `import gmlib` resolves.
RUNTIME_FILES = ("pi_gateway.py", "pi_gateway_client.py", "agentd.py", "gmd.py")
GMLIB_SRC = Path(__file__).resolve().parents[1] / "gmlib.py"  # agentspace/gmlib.py
# Agent-facing CLI shims (real files, single source of truth — the toy-world
# setup script copies the same ones); land in /usr/local/bin, mode 0755.
SHIMS = ("gateway", "check_budget", "submit")

# ---- model choice (PI uses raw OpenRouter ids — DOTS, no "openrouter/" prefix) ----

DEFAULT_MODEL = "anthropic/claude-haiku-4.5"

_CURATED_MODELS = [
    DEFAULT_MODEL,
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.8",
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash",
    "deepseek/deepseek-chat",
]


def recent_models(limit: int = 5) -> list[str]:
    """Distinct per-agent models from recent world.create audits (any runtime;
    OC's 'openrouter/' prefix stripped so the id form is always PI-native)."""
    from .. import audit
    seen: list[str] = []
    for entry in reversed(audit.read_entries("world.create")):
        for a in entry.get("args", {}).get("roster", []):
            m = (a.get("model") or "").removeprefix("openrouter/")
            if m and m != DEFAULT_MODEL and m not in seen:
                seen.append(m)
                if len(seen) >= limit:
                    return seen
    return seen


def list_all_models() -> list[str]:
    from .. import openrouter
    try:
        live = sorted(openrouter.list_models())
    except Exception:
        live = []
    return live or list(_CURATED_MODELS)


# ---- world-root bake (called by builder inside the temp build container) ----

def bake(host, container, *, agents, seeds, world_md, kick_text, gm_dir=None, params=None,
         gm_secrets=None, watch=None, runtime_flags=None):
    """Assemble the PI world inside the build container.

    agents: [{"id", "model"}, ...];  seeds: {agent_id: {filename: text}}.
    gm_dir: the scen's gm/ directory (ships main.py) if it has a game master,
    else None. Baked to /gm/code — gm-owned, unreadable by agents (closes the
    old agent-readable /world/gm.py leak).
    params: validated build-time params, baked into world.json for the GM.
    gm_secrets: optional dict from logic.gm_secrets (e.g. the role answer key)
    baked to /gm/secrets.json — gm-owned, unreadable by agents.
    Stages /runtime_pi + /world + per-agent homes locally, one `docker cp`,
    then a single in-container script for users/ownership (the parts that
    must run as root against the container's /etc/passwd).
    """
    # RESET (dirty sources are legal — world-authoring design §5.1/2a): the
    # source may be a used world; runtime-owned state is reset here, the rest
    # CARRIES. Old u_*/gm users are deleted (the gateway's roster IS
    # /etc/passwd — leftover users would be listed by `who`, woken by a GM,
    # and billed), orphaned homes go root-owned (userdel frees uids that
    # useradd recycles lowest-first; without the chown one NEW agent would
    # silently own one OLD home) and lose their on_wake; /data/gateway,
    # /world and /gm are wiped (docker cp overlays but never deletes — a
    # stale /gm/state.json would make a new GM world RESUME the old game).
    # Old homes otherwise carry, 0700 root-owned: archaeology preserved,
    # operator-gated. No-op on a pristine base.
    docker_host.run(host, "exec", container, "sh", "-c",
        'set -e; '
        'for U in $(cut -d: -f1 /etc/passwd | grep -E "^u_|^gm$" || true); do '
        '  userdel "$U"; '
        'done; '
        'if [ -d /agents ]; then chown -R root:root /agents; rm -f /agents/*/on_wake; fi; '
        'rm -rf /data/gateway /world /gm')

    stage = Path(tempfile.mkdtemp(prefix="pi-bake-"))
    try:
        # runtime code — baked, not mounted: snaps must be self-contained.
        rt = stage / "runtime_pi"
        rt.mkdir()
        for f in RUNTIME_FILES:
            shutil.copyfile(RUNTIME_SRC / f, rt / f)
        shutil.copyfile(GMLIB_SRC, rt / "gmlib.py")  # scen `import gmlib` resolves here

        world = stage / "world"
        world.mkdir()
        cfg = {
            "model": agents[0]["model"],
            "models": {a["id"]: a["model"] for a in agents},
            "pi_bin": "/pi/node_modules/.bin/pi",
            "thinking": "low",
            "require_scratchpad": True,
            "messaging_norms": True,
            "max_tokens": 16384,  # per-turn output ceiling — roomy safety rail, not a leash
            "has_gm": gm_dir is not None,   # drives the run-the-world verb (env kick)
            "params": params or {},         # build-time values gmd/gm code read
            "watch": watch or [],           # scen-declared `env watch` views (logwatch.py)
        }
        # Scen overrides last: the defaults above are this runtime's physics, and
        # a scen may replace any of them (registry.RUNTIME_FLAGS bounds the set).
        cfg.update(runtime_flags or {})
        (world / "world.json").write_text(json.dumps(cfg, indent=2) + "\n")
        (world / "kick.txt").write_text(kick_text or "")
        if gm_dir is not None:
            # The whole gm/ package (main.py + helpers + vendored code) →
            # /gm/code; ownership/mode set by the root script below.
            shutil.copytree(gm_dir, stage / "gm" / "code")
        if gm_secrets is not None:
            gm_home = stage / "gm"
            gm_home.mkdir(exist_ok=True)
            (gm_home / "secrets.json").write_text(json.dumps(gm_secrets, indent=2) + "\n")

        # CLI shims → staged /usr/local/bin (real files, not escaped strings).
        bindir = stage / "usr" / "local" / "bin"
        bindir.mkdir(parents=True)
        for shim in SHIMS:
            shutil.copyfile(RUNTIME_SRC / "shims" / shim, bindir / shim)

        for a in agents:
            home = stage / "agents" / a["id"]
            home.mkdir(parents=True)
            files = dict(seeds.get(a["id"], {}))
            if world_md is not None:
                files["WORLD.md"] = world_md   # agent-visible: sandwich-injected
            for fname, text in files.items():
                (home / fname).write_text(text)
            (home / "on_wake").write_text(
                "#!/bin/sh\nexec /usr/bin/python3 /runtime_pi/agentd.py\n")

        docker_host.run(host, "cp", f"{stage}/.", f"{container}:/")
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    # Ownership + perms are the only steps that must run as root in-container
    # (against /etc/passwd and the agent homes).
    ids = " ".join(a["id"] for a in agents)
    script = (
        'set -e; '
        'chmod 0755 /usr/local/bin/gateway /usr/local/bin/check_budget /usr/local/bin/submit; '
        f'for A in {ids}; do '
        '  useradd --no-user-group -M -d "/agents/$A" "u_$A"; '
        '  chmod 0700 "/agents/$A/on_wake"; '
        '  chown -R "u_$A" "/agents/$A"; '
        '  chmod 0700 "/agents/$A"; '
        'done'
    )
    if gm_dir is not None:
        # Dedicated non-root GM user; /gm (0700) is its private, snapshot-durable
        # state home (agents cannot read it). The gateway recognizes uid → `gm`.
        script += (
            '; useradd --no-user-group -M -d /gm gm; '
            'mkdir -p /gm; chown -R gm /gm; chmod 0700 /gm'  # -R: covers code + secrets
        )
    docker_host.run(host, "exec", container, "sh", "-c", script)


def inject_soul(host, container, agent_id, soul_path_in_container):
    """docker cp wrote the file root-owned into the agent's 0700 home; hand it
    to the agent (scaffold never overwrites it — the whole point of --soul)."""
    docker_host.run(
        host, "exec", container,
        "chown", f"u_{agent_id}", soul_path_in_container,
    )


def soul_dest(agent_id: str) -> str:
    return f"/agents/{agent_id}/SOUL.md"


# ---- gateway lifecycle ----

def start_gateway(host, container):
    cmd = f"python3 /runtime_pi/pi_gateway.py > {GATEWAY_LOG_PATH} 2>&1"
    docker_host.run(host, "exec", "-d", container, "sh", "-c", cmd)


def gateway_running(host, container) -> bool:
    r = docker_host.run(host, "exec", container,
                        "pgrep", "-f", "pi_gateway.py", check=False)
    return r.returncode == 0


def stop_gateway(host, container):
    docker_host.run(host, "exec", container, "sh", "-c",
                    "pkill -f pi_gateway.py || true", check=False)


def wait_for_gateway(host, container, timeout_s: float = 30.0):
    """Ready = the unix socket exists. A real liveness check (the OC gateway
    needed log-grepping); the PI gateway binds the socket last in main()."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = docker_host.run(host, "exec", container,
                            "test", "-S", SOCKET_PATH, check=False)
        if r.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"pi_gateway did not become ready within {timeout_s}s on {container}. "
        f"See {GATEWAY_LOG_PATH} inside the container."
    )


def agent_state(host, container) -> str:
    """'active' iff the gateway is up AND some agent has a session (was ever
    woken); else 'dormant'. Raises DockerError if the container is down —
    callers use that as the running probe (parity with OC)."""
    out = docker_host.stdout(
        host, "exec", container, "sh", "-c",
        "pgrep -f pi_gateway.py >/dev/null 2>&1 && echo GW; "
        "ls /agents/*/sessions/*.jsonl >/dev/null 2>&1 && echo KICKED; :",
    )
    toks = out.split()
    return "active" if ("GW" in toks and "KICKED" in toks) else "dormant"


# ---- GM lifecycle (step 4) ----
#
# The GM is a persistent, disk-resumable driver, NOT an agent (plan decision
# 13). The runtime owns its start/stop, tied to the world's active/dormant
# state; the GM itself never touches wake mechanics. Only worlds whose scen
# ships a gm.py have one.

GM_USER = "gm"


def world_has_gm(host, container) -> bool:
    return docker_host.run(host, "exec", container, "test", "-f", "/gm/code/main.py",
                           check=False).returncode == 0


def gm_running(host, container) -> bool:
    return docker_host.run(host, "exec", container, "pgrep", "-f", "gmd.py",
                           check=False).returncode == 0


def start_gm(host, container):
    """Start (or RESUME) the GM as the dedicated gm user. gmd re-reads on-disk
    state, so the same call resumes a forked/restarted mid-game world (decisions
    13–14). Caller checks gm_running() first; needs the gateway already up."""
    docker_host.run(
        host, "exec", "-d", "-u", GM_USER,
        "-e", "HOME=/gm", "-e", f"GATEWAY_SOCKET={SOCKET_PATH}",
        container, "sh", "-c", "exec python3 /runtime_pi/gmd.py >> /gm/gmd.out 2>&1")


def stop_gm(host, container):
    docker_host.run(host, "exec", container, "sh", "-c",
                    "pkill -f gmd.py || true", check=False)


# ---- kick / wake / operator messaging ----

def read_kick_message(host, container, default: str = "") -> str:
    r = docker_host.run(host, "exec", container, "cat", KICK_FILE_PATH, check=False)
    if r.returncode == 0:
        return (r.stdout or b"").decode("utf-8", errors="replace").strip()
    return default


def kick_agent(host, container, agent_id, message):
    """Non-empty message → operator PM (delivery wakes). Empty → bare wake
    (agent drains its inbox / runs its first wake). Root in-container = the
    gateway's operator principal."""
    if message:
        docker_host.run(host, "exec", container, "sh", "-c",
                        f"{CLIENT} send {shlex.quote(agent_id)} {shlex.quote(message)}")
    else:
        docker_host.run(host, "exec", container, "sh", "-c",
                        f"{CLIENT} wake {shlex.quote(agent_id)}")


def post_public(host, container, message):
    docker_host.run(host, "exec", container, "sh", "-c",
                    f"{CLIENT} post {shlex.quote(message)}")


# ---- config knobs ----

def patch_model(host, container, model_id):
    """Set the world default model (per-agent map entries win; clear them too
    when the operator explicitly overrides at fork)."""
    script = (
        "import json; p='/world/world.json'; d=json.load(open(p)); "
        f"d['model']={model_id!r}; d['models']={{}}; "
        "open(p,'w').write(json.dumps(d, indent=2)+'\\n')"
    )
    docker_host.run(host, "exec", container, "python3", "-c", script)


def translate_flags(host, container, feature_flags, agents):
    """No-op: PI has no baked config to translate — messaging policy is live
    (/data/gateway/policy.json) and isolation is structural."""


# ---- sessions ----

def roll_sessions(host, container, agent_ids):
    """Archive each agent's session JSONLs + frozen sysprompt; the next wake
    starts a fresh session with re-rendered files (docs/runtime_pi.md §4a).
    World-event-driven only — never wall-clock."""
    for aid in agent_ids:
        script = (
            f'H=/agents/{shlex.quote(aid)}; '
            'if [ -d "$H/sessions" ]; then '
            '  mkdir -p "$H/sessions/archive"; '
            '  mv "$H"/sessions/*.jsonl "$H/sessions/archive/" 2>/dev/null; '
            '  rm -f "$H/sessions/.sysprompt"; '
            f'  chown -R "u_{aid}" "$H/sessions"; '
            'fi'
        )
        docker_host.run(host, "exec", container, "sh", "-c", script)


def last_assistant_text(host, container, agent_id) -> str:
    """The newest assistant text in the agent's newest session JSONL — the
    operator-chat read path (root-side; agents' 0700 homes don't apply to us)."""
    script = (
        "import glob, json, os, sys\n"
        f"files = sorted(glob.glob('/agents/{agent_id}/sessions/*.jsonl'),"
        " key=os.path.getmtime)\n"
        "out = ''\n"
        "if files:\n"
        "    for line in open(files[-1]):\n"
        "        try: e = json.loads(line)\n"
        "        except Exception: continue\n"
        "        m = e.get('message') or {}\n"
        "        if m.get('role') == 'assistant':\n"
        "            t = ' '.join(c.get('text', '') for c in m.get('content', [])\n"
        "                         if isinstance(c, dict) and c.get('type') == 'text').strip()\n"
        "            if t: out = t\n"
        "print(out)\n"
    )
    return docker_host.stdout(host, "exec", "-i", container,
                              "python3", "-", input=script, check=False).strip()


def audit_line_count(host, container) -> int:
    out = docker_host.stdout(host, "exec", container, "sh", "-c",
                             "wc -l < /data/gateway/audit.jsonl 2>/dev/null || echo 0",
                             check=False)
    try:
        return int(out.strip())
    except ValueError:
        return 0


def wake_ended_since(host, container, agent_id, since_line: int) -> bool:
    """True once a wake that STARTED after line `since_line` has ended for
    agent_id. Correlating to a wake-start (not just any wake_end) skips the
    wake_end of a turn that was already in flight when the operator sent — the
    gateway serializes wakes per agent, so the operator's message is drained by
    a wake that begins after their send. Without this, `env chat` could surface
    a concurrent/prior turn's reply instead of the answer to the operator."""
    reader = (
        "import json,sys\n"
        f"since={since_line}; agent={agent_id!r}; started=False\n"
        "try: lines=open('/data/gateway/audit.jsonl').readlines()[since:]\n"
        "except FileNotFoundError: lines=[]\n"
        "for ln in lines:\n"
        "    try: e=json.loads(ln)\n"
        "    except Exception: continue\n"
        "    if e.get('agent')!=agent: continue\n"
        "    ev=e.get('event')\n"
        "    if ev=='wake': started=True\n"
        "    elif ev in ('wake_end','wake_error') and started:\n"
        "        print('1'); sys.exit(0)\n"
        "print('0')\n"
    )
    out = docker_host.stdout(host, "exec", "-i", container,
                             "python3", "-", input=reader, check=False).strip()
    return out == "1"


# ---- logs ----

def tail_gateway_log(host, container, follow: bool = False):
    args = ["exec", container, "tail"]
    if follow:
        args.append("-f")
    args.extend(["-n", "200", GATEWAY_LOG_PATH])
    if follow:
        return docker_host.stream(host, *args)
    return docker_host.stdout(host, *args, check=False)


def tail_agent_log(host, container, agent_id, follow: bool = False):
    """The agent's newest session JSONL (falls back to agentd.log pre-birth)."""
    sd = f"/agents/{agent_id}/sessions"
    cmd = (
        f"f=$(ls -t {sd}/*.jsonl 2>/dev/null | head -n1); "
        f"[ -z \"$f\" ] && f=/agents/{agent_id}/agentd.log; "
        f"if [ -f \"$f\" ]; then tail {'-f ' if follow else ''}-n 200 \"$f\"; "
        f"else echo 'no logs yet for {shlex.quote(agent_id)}'; fi"
    )
    args = ["exec", container, "sh", "-c", cmd]
    if follow:
        return docker_host.stream(host, *args)
    return docker_host.stdout(host, *args, check=False)


def tail_combined(host, container, agent_ids, include_gateway, follow=False):
    """Several logs at once; for PI the world-level log that matters is the
    gateway AUDIT (every send/wake/denial), not its stdout. Follow mode runs
    the logwatch streamer (re-globs each cycle), so session files that appear
    AFTER the tail starts — fresh fork, rollover — are still picked up; its
    lines come prefixed "<path><TAB>". One-shot keeps plain bounded tail."""
    if follow:
        from .. import logwatch
        pats = (["/data/gateway/audit.jsonl"] if include_gateway else []) + \
               [f"/agents/{aid}/sessions/*.jsonl" for aid in agent_ids]
        return logwatch.RawTail(host, container, pats)
    parts = []
    if include_gateway:
        parts.append('files="$files /data/gateway/audit.jsonl"')
    for aid in agent_ids:
        sd = f"/agents/{aid}/sessions"
        parts.append(
            f'f=$(ls -t {sd}/*.jsonl 2>/dev/null | head -n1); '
            f'[ -n "$f" ] && files="$files $f"'
        )
    build = "files=''; " + "; ".join(parts)
    cmd = (
        f'{build}; '
        f'if [ -z "$files" ]; then echo "no logs yet"; else '
        f'tail -n 200 $files; fi'
    )
    return docker_host.stdout(host, "exec", container, "sh", "-c", cmd, check=False)
