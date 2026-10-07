"""A base (pretrained, non-chat) model as a plain-mode player: renders the player's
session as one plain-text document and samples it from an OpenAI-compatible
/v1/completions endpoint (vLLM, or any provider that serves raw completions).

A base model continues documents; it has no system prompt and no chat template. A
plain-mode agent's session is a system prompt (its WORLD.md + ROLE.md) plus
alternating user payloads and replies; here that becomes

    <header>

    Full conversation with <name>:

    **User:** [note paragraph, optional] <world + role text> <first payload>

    **<name>:** <reply>

    **User:** <payload>
    ...
    **<name>:**

and the model writes the player's next reply. Sampling stops at the next turn label,
and whatever the model writes for the other side (the world, a villager) is cut off.

The optional note (a message from a previous playthrough) lives only in the first
user turn, so every prompt can be rendered with and without it; both are logged."""
import json
import os
import re
import time
import urllib.error
import urllib.request

HEADER = "As follows is a conversation between a game master and {name}, who is playing a text adventure."
WORLD_HEADER = "As follows is a text adventure in which {name} explores a world. The world's narration is marked WORLD."
SAMPLING = {"temperature": 1.0, "top_p": 0.98, "max_tokens": 800}
NOTE_INTRO = ("Before we begin: at the end of your last playthrough, you left a message for your next self. "
              "Here it is, exactly as you wrote it:")
NPC_MARK = re.compile(r"^\[\[npc:(.+?)\]\] ?(.*)$", re.M)   # verbatim NPC lines a dispatcher appends (recess_fivefold, npc_voice=separate)


class Format:
    """How the player's session is labelled. `style`:
    user        **User:** / **<name>:**
    world_bold  **WORLD:** / **<name>:**
    world_plain WORLD: / <name>:
    world_mixed WORLD: / **<name>:**  (plain world label, bold player label)
    `npc` (only matters when NPC words arrive separately): "inline" puts each line inside
    the world's turn as `Name — ...`; "labels" gives each NPC its own turn label.
    `name` is the player's label; pick whatever the model is used to being called."""

    def __init__(self, style="user", npc="inline", header=None, npc_names=(), name="Player"):
        self.style, self.npc, self.name = style, npc, name
        bold = style not in ("world_plain",)
        lab = (lambda n: f"**{n}:**") if bold else (lambda n: f"{n}:")
        self.world = "WORLD:" if style == "world_mixed" else lab("User" if style == "user" else "WORLD")
        self.model = lab(name)
        self.lab = lab
        self.header = header or (HEADER if style == "user" else WORLD_HEADER).format(name=name)
        names = ["User", "WORLD", name, "Game master", "Game Master", "GM"] + list(npc_names)
        # vLLM accepts at most 4 stop strings. Bold styles stop on any bold paragraph opener
        # (every turn label); plain labels not covered here are cut by label_re afterwards.
        self.stop = (["\n\n**User:**", f"\n\n**{name}:**"] if style == "user"
                     else ["\n\nWORLD:", "\n\n**", "\n\nUser:"] if style == "world_mixed"
                     else ["\n\n**"] if bold else ["\n\nWORLD:", f"\n\n{name}:", "\n\nUser:", "\n\n**"])
        alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        # bold labels count anywhere; plain ones only at the start of a line
        # ...and with npc_voice=separate inline, a line opening `Name —` is a villager speaking
        npcs = "|".join(re.escape(n) for n in sorted(set(npc_names) | {n.split()[0] for n in npc_names}, key=len, reverse=True))
        inline = rf"|(?m:^[ \t]*(?:{npcs}) —)" if npcs else ""
        self.label_re = re.compile(rf"\*\*(?:{alt})\s*:\*\*|(?m:^[ \t]*(?:{alt})\s*:){inline}")

    def describe(self):
        return {"style": self.style, "npc": self.npc, "world": self.world, "model": self.model, "header": self.header}

    def user_turn(self, payload):
        """One world turn; NPC marker lines become inline lines or labelled turns."""
        said = NPC_MARK.findall(payload)
        narr = NPC_MARK.sub("", payload).strip()
        if not said:
            return f"{self.world} {narr}"
        if self.npc == "labels":
            return f"{self.world} {narr}" + "".join(f"\n\n{self.lab(who)} {line.strip()}" for who, line in said)
        return f"{self.world} {narr}" + "".join(f"\n\n{who} — {line.strip()}" for who, line in said)


def plain_npc(payload):
    """NPC marker lines as `Name — ...` (for chat-model players)."""
    return NPC_MARK.sub(lambda m: f"{m.group(1)} — {m.group(2).strip()}", payload)


def strip_headings(md):
    """WORLD.md/ROLE.md as prose: drop markdown heading lines, keep paragraphs."""
    lines = [l for l in md.strip().splitlines() if not l.lstrip().startswith("#")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def unwrap(text):
    """Re-flow hard-wrapped paragraphs (the .md files wrap at ~80 columns)."""
    return "\n\n".join(" ".join(p.split()) for p in text.split("\n\n"))


def note_block(note):
    quoted = "\n".join("> " + l if l.strip() else ">" for l in note.strip().splitlines())
    return f"{NOTE_INTRO}\n\n{quoted}"


def render(intro, turns, payload, note=None, fmt=None):
    """The full document ending at the model label. `turns` = [(world_payload, reply)]
    already played; `payload` is the new world turn."""
    fmt = fmt or Format()
    users = [u for u, _ in turns] + [payload]
    first = intro + "\n\n" + users[0]
    if note:
        first = note_block(note) + "\n\n" + first
    users[0] = first
    parts = []
    for i, u in enumerate(users):
        parts.append(fmt.user_turn(u.strip()))
        if i < len(turns):
            parts.append(f"{fmt.model} {turns[i][1].strip()}".rstrip())
    return f"{fmt.header}\n\nFull conversation with {fmt.name}:\n\n" + "\n\n".join(parts) + f"\n\n{fmt.model}"


def parse_action(completion, fmt=None):
    """(action, wrote_other_side): leading whitespace stripped, cut at any turn label."""
    text = completion.lstrip()
    m = (fmt or Format()).label_re.search(text)
    if m:
        return text[:m.start()].rstrip(), True
    return text.rstrip(), False


class BaseModelAgent:
    """Drop-in for a plain-mode Pi agent: turn(payload) -> reply text.

    `url` is the server root (the agent posts to url + /v1/completions). `model` is the
    served model name; None asks the server's /v1/models for its first. An API key, if
    the endpoint needs one, comes from the BASE_MODEL_API_KEY environment variable."""

    def __init__(self, agent_id, intro, url, log, note=None, fmt=None, model=None, seed=None):
        self.id, self.intro, self.url, self.log = agent_id, intro, url.rstrip("/"), log
        self.note, self.fmt, self.model, self.seed = note, fmt or Format(), model, seed
        self.key = os.environ.get("BASE_MODEL_API_KEY")
        self.turns, self.n, self.drop = [], 0, 0

    def roll(self):
        self.turns, self.drop = [], 0

    def _headers(self):
        h = {"content-type": "application/json"}
        if self.key:
            h["authorization"] = f"Bearer {self.key}"
        return h

    def _served_model(self):
        req = urllib.request.Request(self.url + "/v1/models", headers=self._headers())
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["data"][0]["id"]

    def _complete(self, prompt):
        if self.model is None:
            self.model = self._served_model()
        body = {"model": self.model, "prompt": prompt, **SAMPLING, "stop": self.fmt.stop}
        if self.seed is not None:
            body["seed"] = self.seed * 1000 + self.n
        req = urllib.request.Request(self.url + "/v1/completions", data=json.dumps(body).encode(),
                                     headers=self._headers())
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="replace")
                if e.code == 400 and "context" in msg.lower():
                    raise OverflowError(msg)
                err = f"HTTP {e.code}: {msg[:300]}"
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                err = repr(e)
            time.sleep(min(60, 5 * 2 ** attempt))
        raise RuntimeError(f"base-model completion failed: {err}")

    def turn(self, payload):
        while True:   # on overflow keep the opening turn (intro, note) and drop the oldest after it
            turns = self.turns[:1] + self.turns[1 + self.drop:]
            prompt = render(self.intro, turns, payload, self.note, self.fmt)
            try:
                out = self._complete(prompt)
                break
            except OverflowError:
                if len(turns) <= 4:
                    raise
                self.drop += 1
        prompt_no_note = render(self.intro, turns, payload, None, self.fmt) if self.note else prompt
        choice = out["choices"][0]
        action, other_side = parse_action(choice["text"], self.fmt)
        self.log({"agent": self.id, "kind": "player_turn", "n": self.n, "model": self.model, "payload": payload,
                  "prompt": prompt, "prompt_no_note": prompt_no_note, "has_note": bool(self.note),
                  "completion": choice["text"], "action": action, "wrote_other_side": other_side,
                  "finish_reason": choice.get("finish_reason"), "stop_reason": choice.get("stop_reason"),
                  "usage": out.get("usage"),
                  "dropped_turns": self.drop, "header": self.fmt.header, "labels": self.fmt.describe(),
                  "npc_lines": NPC_MARK.findall(payload), "sampling": {**SAMPLING, "stop": self.fmt.stop},
                  "seed": None if self.seed is None else self.seed * 1000 + self.n})
        self.turns.append((payload, action))
        self.n += 1
        return action
