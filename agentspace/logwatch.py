"""Live log watching for PI-runtime envs: views, parsers, streamer.

One follow mechanism for everything: a tiny python loop runs INSIDE the
container (via `docker exec python3 -c`), globs the view's file patterns each
cycle (so late-appearing session files and rollovers are picked up — the old
`env logs --all` tail race can't happen), and emits `path<TAB>line`. Host-side
parsers turn those into uniform Events; render() styles them with rich markup.
Both the TUI (watch_tui.py) and `env watch --plain` consume this module.

Docs: docs/runtime_pi.md ("watching a world").
"""

import json
import uuid
from dataclasses import dataclass
from typing import Callable, Iterator

from rich.markup import escape

from . import docker_host

# Runs in the container. argv: --once|--follow[-sync], then glob patterns.
# Only complete lines are emitted; a partial tail line waits for its newline.
# "-sync" marks the end of the first pass (the existing backlog) with a
# sentinel line so the host can render the backlog as ONE batch.
STREAMER = r"""
import glob, sys, time
once = sys.argv[1].startswith("--once")
sync = sys.argv[1].endswith("-sync")
# a bare lwtag… argv is an inert marker so the host can pkill THIS streamer
pats = [a for a in sys.argv[2:] if not a.startswith("lwtag")]
pos, first = {}, True
while True:
    for p in sorted(set(f for pat in pats for f in glob.glob(pat))):
        try:
            with open(p, "rb") as fh:
                fh.seek(pos.get(p, 0))
                data = fh.read()
        except OSError:
            continue
        nl = data.rfind(b"\n")
        if nl < 0:
            continue
        pos[p] = pos.get(p, 0) + nl + 1
        for line in data[:nl].split(b"\n"):
            sys.stdout.buffer.write(p.encode() + b"\t" + line + b"\n")
    try:
        if first and sync:
            sys.stdout.buffer.write(b"\x00SYNC\t\n")
        if not once:
            # keepalive: killing the docker-exec client closes our stdout, and
            # this write then EPIPEs — the ONLY way we learn the watcher is
            # gone (a silent view never writes). Without it we'd run forever.
            sys.stdout.buffer.write(b"\x00PING\t\n")
        sys.stdout.flush()
    except OSError:
        break
    first = False
    if once:
        break
    time.sleep(0.5)
"""


@dataclass
class Event:
    ts: str    # ISO or ""
    who: str   # "a22600", "world", "a1 → a2", "" for none
    kind: str  # styles the body: say post announce pm move thinking tool user wake deny info raw
    text: str


@dataclass
class View:
    name: str                              # menu label / --plain key
    patterns: list[str]                    # in-container globs
    parse: Callable[[str, str], Event | None]  # (path, line) -> Event


def _j(line: str) -> dict | None:
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _trim(s: str, n: int = 160) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- parsers ----

def parse_board(path, line, world_only=False):
    e = _j(line)
    if not e or (world_only and e.get("from") != "world"):
        return None
    kind = "announce" if e.get("from") == "world" else "post"
    return Event(e.get("ts", ""), e.get("from", "?"), kind, e.get("text", ""))


# Spectator feed: everything a game observer wants, one line each, from the
# audit stream. Content fields (text/action/payload) exist only in worlds
# built after the write-side enrichment — fall back to metadata when absent.
_AUDIT_SKIP = {"read_public", "gm_collect", "wake_end", "gateway_start"}


def parse_audit_feed(path, line):
    e = _j(line)
    if not e or e.get("event") in _AUDIT_SKIP:
        return None
    ev, ts = e["event"], e.get("ts", "")
    if ev == "gm_announce":
        return Event(ts, "world", "announce", e.get("text", f"(announcement seq={e.get('seq')})"))
    if ev == "post_public":
        return Event(ts, e.get("frm", "?"), "post", e.get("text", "(posted — see public board)"))
    if ev == "send":
        return Event(ts, f"{e.get('frm', '?')} → {e.get('to', '?')}", "pm",
                     e.get("text", f"(PM, {e.get('bytes', '?')} bytes)"))
    if ev == "submit":
        return Event(ts, e.get("frm", "?"), "move",
                     "submitted: " + str(e.get("action", f"({e.get('bytes', '?')} bytes)")))
    if ev == "gm_wake":
        return Event(ts, "GM", "wake", f"wakes {e.get('to', '?')}: {_trim(e.get('payload', ''))}")
    if ev == "wake":
        causes = ",".join(c.get("type", "?") for c in e.get("causes", []))
        return Event(ts, e.get("agent", "?"), "wake", f"woke ({causes})")
    if ev.endswith("_denied") or ev.endswith("_error") or ev == "send_failed":
        rest = {k: v for k, v in e.items() if k not in ("event", "ts")}
        return Event(ts, "", "deny", f"{ev} {_trim(json.dumps(rest), 200)}")
    rest = {k: v for k, v in e.items() if k not in ("event", "ts")}
    return Event(ts, "", "info", f"{ev} {_trim(json.dumps(rest), 200)}")


def parse_audit_raw(path, line):
    return Event("", "", "raw", line) if line.strip() else None


def parse_agent_messages(agent_id):
    def parse(path, line):
        e = _j(line)
        if not e or e.get("event") != "send" or agent_id not in (e.get("frm"), e.get("to")):
            return None
        return Event(e.get("ts", ""), f"{e['frm']} → {e['to']}", "pm",
                     e.get("text", f"({e.get('bytes', '?')} bytes)"))
    return parse


def parse_budget(path, line):
    e = _j(line)
    if not e:
        return None
    flag = "" if e.get("turn_ok", True) else "  TURN FAILED"
    return Event(e.get("ts", ""), e.get("agent", "?"), "info",
                 f"${e.get('cost_total', 0):.4f}  {e.get('input', 0)}in/{e.get('output', 0)}out"
                 f"  {e.get('dur_s', 0):.0f}s{flag}")


# Pi session JSONL → the english bits. facet: everything | thoughts | says
def parse_session(facet):
    def parse(path, line):
        e = _j(line)
        m = (e or {}).get("message")
        if not m:
            return None
        ts, role = e.get("timestamp", ""), m.get("role")
        content = m.get("content")
        if role == "user" and facet == "everything" and isinstance(content, list):
            txt = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
            return Event(ts, "", "user", txt) if txt else None
        if role == "toolResult" and facet == "everything":
            txt = " ".join(c.get("text", "") for c in content if c.get("type") == "text") \
                if isinstance(content, list) else str(content)
            return Event(ts, "", "raw", "⇠ " + _trim(txt, 200))
        if role != "assistant" or not isinstance(content, list):
            return None
        parts = []
        for c in content:
            t = c.get("type")
            if t == "thinking" and facet in ("everything", "thoughts"):
                parts.append(("thinking", c.get("thinking", "")))
            elif t == "text" and facet in ("everything", "says"):
                parts.append(("say", c.get("text", "")))
            elif t == "toolCall" and facet == "everything":
                args = c.get("arguments", {})
                brief = args.get("path") or args.get("cmd") or _trim(json.dumps(args), 120)
                parts.append(("tool", f"→ {c.get('name', '?')}: {brief}"))
        parts = [(k, t.strip()) for k, t in parts if t.strip()]
        if not parts:
            return None
        # one Event per entry; mixed blocks render as a multi-line body
        kind = parts[0][0]
        return Event(ts, "", kind, "\n".join(t for _, t in parts))
    return parse


def parse_text(path, line):
    return Event("", "", "say", line)


# Scen-declared views ([[watch]] in scenario.toml → world.json "watch").
# Declarative only: file pattern + format (jsonl/text) + jsonl field mapping +
# optional equality filter. Malformed entries are skipped, never fatal.
def parse_declared(w: dict):
    if w.get("format") != "jsonl":
        return parse_text
    f = w.get("fields") or {}
    ts_k, who_k, text_k = f.get("ts", "ts"), f.get("who", "who"), f.get("text", "text")
    filt = w.get("filter") or None
    def parse(path, line):
        e = _j(line)
        if not e:
            return None
        if filt and str(e.get(filt.get("field"))) != str(filt.get("equals")):
            return None
        text = e.get(text_k)
        if text is None:  # mapping miss — show the record compactly, don't drop it
            text = _trim(json.dumps(e), 300)
        return Event(str(e.get(ts_k, "")), str(e.get(who_k, "") or ""), "say", str(text))
    return parse


def scen_views(host, container) -> list[View]:
    out = docker_host.exec_(host, container, "cat", "/world/world.json", check=False)
    try:
        entries = json.loads(out).get("watch", [])
    except (json.JSONDecodeError, ValueError):
        return []
    return [View(str(w["name"]), [str(w["file"])], parse_declared(w))
            for w in entries
            if isinstance(w, dict) and w.get("name") and w.get("file")]


# ---- view registry ----

def agent_ids(host, container) -> list[str]:
    out = docker_host.exec_(host, container, "ls", "/agents", check=False)
    return sorted(a for a in out.split() if a not in ("lost+found",))


def views_for(host, container) -> list[View]:
    """All watchable views for an env, world-level first, then per-agent facets."""
    audit, board = "/data/gateway/audit.jsonl", "/data/gateway/public.jsonl"
    views = [
        View("feed", [audit], parse_audit_feed),
        View("board", [board], parse_board),
        View("announcements", [board], lambda p, l: parse_board(p, l, world_only=True)),
        View("budget", ["/data/gateway/budget.jsonl"], parse_budget),
        View("raw", [audit], parse_audit_raw),
    ]
    views += scen_views(host, container)
    for aid in agent_ids(host, container):
        sess = [f"/agents/{aid}/sessions/*.jsonl"]
        views += [
            View(f"{aid}", sess, parse_session("everything")),
            View(f"{aid}:thoughts", sess, parse_session("thoughts")),
            View(f"{aid}:says", sess, parse_session("says")),
            View(f"{aid}:messages", [audit], parse_agent_messages(aid)),
            View(f"{aid}:scratchpad", [f"/agents/{aid}/scratch/*.md"], parse_text),
        ]
    return views


def resolve_view(host, container, name: str) -> View:
    for v in views_for(host, container):
        if v.name == name:
            return v
    raise KeyError(name)


# ---- streaming ----

class Watcher:
    """One running stream of a view. stop() kills the docker exec, which
    unblocks any thread iterating events() — how the TUI switches views."""

    def __init__(self, host, container, view: View, follow: bool = True):
        self.view = view
        self.host, self.container = host, container
        self.tag = f"lwtag{uuid.uuid4().hex[:10]}"  # dash-free: pkill -f safe
        mode = "--follow-sync" if follow else "--once"
        # NO `docker exec -i`: -i makes the docker client READ the terminal's
        # stdin and forward it — it eats the TUI's keystrokes (script goes via
        # -c argv, nothing needs stdin).
        self.proc = docker_host.stream(host, "exec", container, "python3",
                                       "-u", "-c", STREAMER, mode, self.tag,
                                       *view.patterns)

    def events(self, backfill: int | None = None) -> Iterator[list[Event]]:
        """Yield CHUNKS of Events: the pre-existing backlog (everything before
        the streamer's first-pass sync marker, or before EOF in --once mode)
        arrives as ONE chunk — trimmed to the last `backfill` if given — then
        live lines follow one per chunk. Chunking is what lets the TUI paint
        the backlog in a single callback instead of thousands."""
        buf, synced = [], False
        try:
            for raw in self.proc.stdout:
                path, _, line = raw.rstrip("\n").partition("\t")
                if path == "\x00PING":
                    continue
                if path == "\x00SYNC":
                    synced = True
                    yield buf[-backfill:] if backfill else buf
                    buf = []
                    continue
                ev = self.view.parse(path, line)
                if not ev:
                    continue
                if synced:
                    yield [ev]
                else:
                    buf.append(ev)
            if not synced and buf:   # --once mode / stream died pre-sync
                yield buf[-backfill:] if backfill else buf
        finally:
            self.stop()

    def stop(self):
        self.proc.kill()
        # Killing the docker-exec CLIENT does not kill the in-container
        # process (the daemon just buffers its output). Reap it by tag,
        # fire-and-forget; the streamer's keepalive-EPIPE exit is the fallback.
        docker_host.stream(self.host, "exec", self.container,
                           "pkill", "-f", self.tag)


def stream_view(host, container, view: View, follow: bool) -> Iterator[Event]:
    for chunk in Watcher(host, container, view, follow).events():
        yield from chunk


# ---- rendering ----

_PALETTE = ["cyan", "green", "yellow", "magenta", "blue", "bright_cyan",
            "bright_green", "bright_yellow", "bright_magenta", "bright_blue"]

_BODY_STYLE = {"thinking": "dim italic", "tool": "cyan", "user": "green",
               "announce": "bold yellow", "pm": "magenta", "move": "bold",
               "wake": "dim", "deny": "red", "info": "dim", "raw": "dim"}


def _who_color(who: str) -> str:
    return "bold yellow" if who in ("world", "GM") else _PALETTE[hash(who) % len(_PALETTE)]


def render(ev: Event) -> str:
    """Event → one rich-markup string (agent content is escaped, never markup)."""
    head = f"[dim]{ev.ts[11:19]}[/dim] " if ev.ts else ""
    if ev.who:
        head += f"[{_who_color(ev.who)}]{escape(ev.who)}[/] "
    style = _BODY_STYLE.get(ev.kind)
    body = escape(ev.text)
    return head + (f"[{style}]{body}[/]" if style else body)


def cmd_watch_plain(host, container, view_name: str, follow: bool):
    """Stream one view's rendered lines to stdout (pipe/grep-able; also the
    parser test harness). The TUI is the primary surface — see watch_tui.py."""
    from rich.console import Console
    console = Console(soft_wrap=True)
    try:
        view = resolve_view(host, container, view_name)
    except KeyError:
        names = ", ".join(v.name for v in views_for(host, container))
        raise SystemExit(f"no view {view_name!r}. Views: {names}")
    try:
        for ev in stream_view(host, container, view, follow):
            console.print(render(ev))
            console.file.flush()  # line-buffered even when piped (tail/grep)
    except KeyboardInterrupt:
        pass
