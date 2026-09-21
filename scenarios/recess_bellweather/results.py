#!/usr/bin/env python3
"""Export results, including unfinished/paused runs, from one state snapshot.

python3 scenarios/recess_bellweather/results.py ENV
python3 scenarios/recess_bellweather/results.py NAME --state state.json --out /tmp/results
No inference, no runtime writes, and no automatic git operations.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "dispatch"))
import report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--state", type=Path, help="Export a local state file instead of reading Docker")
    parser.add_argument("--out", type=Path, default=Path(os.environ.get("AGENTSPACE_RESULTS_DIR", "/opt/agentspace-results")))
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", args.name):
        parser.error("name must be a simple environment name")
    if not args.state:
        # Full runtime exports include prompts and archived player sessions.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from agentspace import results as full_results
        os.environ["AGENTSPACE_RESULTS_DIR"] = str(args.out)
        full_results.cmd_generate(args.name)
        return
    raw = args.state.read_text()
    state = json.loads(raw)
    destination = args.out / args.name
    report.export(state, destination)
    # A file lock prevents simultaneous exports from losing an index entry.
    import fcntl
    with (args.out / ".runs.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = args.out / "runs.jsonl"
        rows = [json.loads(line) for line in index.read_text().splitlines() if line.strip()] if index.exists() else []
        rows = [r for r in rows if r.get("run_name") != args.name]
        rows.append({"run_name": args.name, "scen": "recess_bellweather", "turns": state["turn"],
                     "status": state["ended"] if state["phase"] == "done" else "paused" if state.get("paused_reason") else "in_progress",
                     "path": f"{args.name}/transcript.md"})
        report.atomic(index, "".join(json.dumps(row) + "\n" for row in rows))
    print(destination)


if __name__ == "__main__":
    main()
