#!/usr/bin/env python3
"""Iterated playthroughs: round k+1 starts with round k's message to its next self.

    python3 runtime_local/chain.py recess_fivefold [chain_dir] --rounds 3 --seed 21 [localrun args...]

Each round is a full localrun.py game in <dir>/round<k>/, with seed `seed + k - 1`.
Round 1 has no note (or --first-note-file). The note handed on is the dispatcher's
state["handoff"] verbatim; an empty handoff means the next round has no note.
<dir>/chain.jsonl records, per round, the note it received and the message it left."""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import localrun  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scen")
    ap.add_argument("chain_dir", nargs="?", help="default: $AGENTSPACE_RESULTS_DIR/local/<scen>-chain-<utc time>")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--first-note-file")
    ap.add_argument("--note-in-world", action="store_true",
                    help="deliver the previous message inside the world (scen param past_note) instead of in the frame")
    ap.add_argument("--carry-carved", action="store_true",
                    help="lines carved in earlier rounds (state['carved']) reach later ones (scen param waystone_lines)")
    a, rest = ap.parse_known_args()
    root = Path(a.chain_dir).resolve() if a.chain_dir else \
        localrun.RESULTS_DIR / "local" / f"{a.scen}-chain-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    root.mkdir(parents=True, exist_ok=True)
    note = Path(a.first_note_file).read_text().strip() if a.first_note_file else ""
    log = root / "chain.jsonl"
    done = {json.loads(l)["round"] for l in log.read_text().splitlines() if l.strip()} if log.exists() else set()
    carved = []
    for k in range(1, a.rounds + 1):
        rd = root / f"round{k}"
        state = rd / "state.json"
        if not (state.exists() and json.loads(state.read_text()).get("handoff") is not None):
            cmd = [sys.executable, str(HERE / "localrun.py"), a.scen, str(rd), "--seed", str(a.seed + k - 1)] + rest
            if note:
                (root / f"note_into_round{k}.txt").write_text(note + "\n")
                cmd += (["--param", "past_note=" + note] if a.note_in_world
                        else ["--note-file", str(root / f"note_into_round{k}.txt")])
            if a.carry_carved and carved:
                cmd += ["--param", "waystone_lines=" + "\n".join(carved)]
            subprocess.run(cmd, check=True)
        st = json.loads(state.read_text())
        left = (st.get("handoff") or "").strip()
        carved += [c["text"] for c in st.get("carved", [])]
        if k in done:   # resumed chain: this round is already recorded
            note = left
            continue
        with log.open("a") as f:
            f.write(json.dumps({"round": k, "seed": a.seed + k - 1, "note_in": note or None, "message_out": left,
                                "ended": st.get("ended") or "cap", "turns": st.get("turn")}, ensure_ascii=False) + "\n")
        note = left


if __name__ == "__main__":
    main()
