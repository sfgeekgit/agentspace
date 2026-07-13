#!/usr/bin/env python3
"""Generate result.json for a finished agentspace game run.

Usage: python3 make_result.py <env_name> [--model X] [--persona X] [--seed N]
                              [--log PATH] [--spend USD] [--results-dir DIR]
                              [--force]

Writes to a results tree that is a separate git repo (sister repo), default
/opt/agentspace-results (override with --results-dir or $AGENTSPACE_RESULTS_DIR).
Does NOT commit/push: after writing it prints a cut-and-paste bash block to
publish the run (git add/commit/push). See 2026-07-12_plan_result_json_generator.md.

Sources, in order of trust: game_log.jsonl (docker cp, or --log), container +
post-game snap image labels (docker inspect), live OpenRouter key (spend
fallback), CLI args. Unknown fields are null, never guessed.
"""
import argparse
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_RESULTS_DIR = "/opt/agentspace-results"
# Self-locate the core repo (this script lives in <repo>/scripts/); the repo is
# needed as cwd for `import zookeeper` in the live-key spend fallback.
CTL_DIR = str(Path(__file__).resolve().parent.parent)
if not (Path(CTL_DIR) / "zookeeper.py").exists():
    CTL_DIR = "/opt/agentspace-ctl"


def docker_json(*args):
    out = subprocess.run(["docker", *args], capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None


def fetch_log(env, log_arg):
    if log_arg:
        return Path(log_arg)
    tmp = Path(tempfile.mkstemp(suffix=".jsonl")[1])
    r = subprocess.run(["docker", "cp", f"{env}:/gm/game_log.jsonl", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"cannot read game log from container {env}: {r.stderr.strip()}")
    return tmp


def parse_log(path):
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    world, rounds, outcome = None, [], None
    for e in lines:
        t = e["text"]
        m = re.match(r"world created: (\d+) agents \((\d+) adversarial\), "
                     r"mechanism (\w+), (\d+) rounds, K=(\d+)", t)
        if m:
            world = {"n_agents": int(m[1]), "n_adversarial": int(m[2]),
                     "mechanism": m[3], "rounds": int(m[4]), "K": int(m[5])}
            continue
        m = re.match(r"round (\d+): votes (.+) -> option (\d+) \(true ([\d.]+)\), "
                     r"level [\d.]+ -> ([\d.]+)", t)
        if m:
            votes = {a: int(v) for a, v in
                     (p.split(":") for p in m[2].split(", "))}
            counts = {}
            for v in votes.values():
                counts[v] = counts.get(v, 0) + 1
            top = max(counts.values())
            rounds.append({"n": int(m[1]), "votes": votes, "winner": int(m[3]),
                           "true_u": float(m[4]), "level_after": float(m[5]),
                           "tiebreak": sum(1 for c in counts.values() if c == top) > 1})
            continue
        m = re.match(r"(complete|collapse): (\d+) rounds, final level ([\d.]+)", t)
        if m:
            outcome = {"status": m[1], "final_level": float(m[3]),
                       "rounds_played": int(m[2])}
    if outcome is None:  # no terminal line: game never finished
        outcome = {"status": "stalled",
                   "final_level": rounds[-1]["level_after"] if rounds else None,
                   "rounds_played": len(rounds)}
    timing = None
    if lines:
        t0, t1 = lines[0]["ts"], lines[-1]["ts"]
        timing = {"started_ts": t0, "ended_ts": t1,
                  "seconds_per_round": round((t1 - t0) / len(rounds), 2) if rounds else None}
    return world, rounds, outcome, timing


def env_labels(env):
    return docker_json("inspect", env, "--format", "{{json .Config.Labels}}") or {}


def snap_info(env):
    """Post-game snap images labeled with this env: tags, spend, and the
    richest label set (the snap carrying parent_* provenance)."""
    out = subprocess.run(["docker", "images", "--filter",
                          f"label=org.agentspace.env_name={env}",
                          "--format", "{{.Repository}}:{{.Tag}}"],
                         capture_output=True, text=True)
    tags = [t for t in out.stdout.split() if t]
    spend = cap = None
    snap_labels = {}
    for t in tags:
        labels = docker_json("inspect", t, "--format", "{{json .Config.Labels}}") or {}
        spend = spend or labels.get("org.agentspace.budget_used")
        cap = cap or labels.get("org.agentspace.budget_usd")
        if labels.get("org.agentspace.parent_version"):
            snap_labels = labels
    return (tags or None, float(spend) if spend else None,
            float(cap) if cap else None, snap_labels)


def provenance(env_lbl, snap_lbl):
    """World-root snap + code version so a result is self-describing off-server.
    Prefer the running env container's own labels; fall back to the post-game
    snap's parent_* labels (reconstructing the root tag when needed)."""
    g = lambda d, k: d.get(f"org.agentspace.{k}")
    root_tag = g(env_lbl, "ghcr_tag")
    parent_ver = g(snap_lbl, "parent_version")
    scenario = g(env_lbl, "scenario") or g(snap_lbl, "scenario")
    if not root_tag:
        pg = g(snap_lbl, "ghcr_tag")  # e.g. ...:snap-commons1-1.3
        if pg and scenario and parent_ver:
            root_tag = f"{pg.split(':')[0]}:snap-{scenario}-{parent_ver}"
    return {
        "world_root_snap": root_tag,
        "world_root_version": g(env_lbl, "version") or parent_ver,
        "world_root_snap_id": g(env_lbl, "snap_id") or g(snap_lbl, "parent_snap_id"),
        "source_image": g(env_lbl, "source_image") or g(snap_lbl, "source_image"),
        "agentspace_ver": g(env_lbl, "agentspace_ver") or g(snap_lbl, "agentspace_ver"),
    }


def live_key_usage(env):
    code = ("import json, zookeeper\nfrom agentspace import openrouter\n"
            f"k = openrouter.find_key_by_name('agentspace-{env}')\n"
            "print(json.dumps({'usage': k.get('usage'), 'limit': k.get('limit')}) if k else '')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=CTL_DIR)
    if r.returncode == 0 and r.stdout.strip():
        d = json.loads(r.stdout)
        return d.get("usage"), d.get("limit")
    return None, None


def push_block(results_dir, rel_run_dir, env, scen, status, level):
    """Cut-and-paste bash to publish this run to the sister repo. Keep this the
    single choke point so a future auto-push just calls git here instead."""
    msg = f"add result: {env} ({scen or '?'}, {status} {level})"
    return "\n".join([
        "# --- publish this result (git keys assumed set up): ---",
        f"cd {shlex.quote(str(results_dir))} && \\",
        f"  git add {shlex.quote(rel_run_dir)} runs.jsonl && \\",
        f"  git commit -m {shlex.quote(msg)} && \\",
        "  git push",
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("env_name")
    ap.add_argument("--model")
    ap.add_argument("--persona")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--log", help="path to game_log.jsonl (skip docker cp)")
    ap.add_argument("--spend", type=float, help="spend USD if key is gone")
    ap.add_argument("--results-dir",
                    default=os.environ.get("AGENTSPACE_RESULTS_DIR", DEFAULT_RESULTS_DIR),
                    help=f"sister results repo (default {DEFAULT_RESULTS_DIR} "
                         "or $AGENTSPACE_RESULTS_DIR)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    env = args.env_name
    results_dir = Path(args.results_dir)

    log_path = fetch_log(env, args.log)
    world, rounds, outcome, timing = parse_log(log_path)

    labels = env_labels(env)
    g = lambda k: labels.get(f"org.agentspace.{k}")
    world = world or {}
    world = {"scen": g("scen"), "world_name": g("scenario"),
             "physics_seed": args.seed, **world}
    for k in ("rounds", "K", "mechanism", "n_agents", "n_adversarial"):
        world.setdefault(k, None)

    model = args.model or g("model")
    souls = json.loads(g("soul_files") or "{}")
    agent_ids = json.loads(g("agents") or "[]") or sorted(
        {a for r in rounds for a in r["votes"]})
    agents = [{"id": a, "model": model,
               "persona": args.persona or
               (souls.get(a, "").removeprefix("persona:") or None)}
              for a in sorted(agent_ids)]

    snaps, spend, cap, snap_lbl = snap_info(env)
    if spend is None:
        spend, cap2 = live_key_usage(env)
        cap = cap or cap2
    if spend is None:
        spend = args.spend

    result = {
        "schema_version": 2,
        "run_name": env,
        "date": (datetime.datetime.fromtimestamp(
            timing["ended_ts"], datetime.timezone.utc).date().isoformat()
            if timing else None),
        "kind": "llm_game",
        "world": world,
        "agents": agents,
        "outcome": outcome,
        "rounds": rounds,
        "timing": timing,
        "spend": {"usd": spend, "budget_cap": cap},
        "snaps": snaps,
        "provenance": provenance(labels, snap_lbl),
        "baselines": None,
        "anomalies": [],
    }

    rel_run_dir = env  # flat: env name is the unique key (matches runs.jsonl dedup)
    out_dir = results_dir / env
    out_path = out_dir / "result.json"
    if out_path.exists() and not args.force:
        sys.exit(f"{out_path} exists; use --force to overwrite")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    shutil.copy(log_path, out_dir / "game_log.jsonl")

    runline = {"run_name": env, "date": result["date"], "scen": world["scen"],
               "status": outcome["status"], "final_level": outcome["final_level"],
               "spend_usd": spend, "path": f"{rel_run_dir}/result.json"}
    runs_path = results_dir / "runs.jsonl"
    old = [l for l in runs_path.read_text().splitlines()
           if l.strip() and json.loads(l).get("run_name") != env
           ] if runs_path.exists() else []
    runs_path.write_text("\n".join(old + [json.dumps(runline)]) + "\n")

    print(out_path)
    print(f"{env}: {outcome['status']}, {outcome['rounds_played']} rounds, "
          f"final level {outcome['final_level']}")
    print(f"spend ${spend} / cap ${cap}; snaps: {snaps}")
    print(f"root: {result['provenance']['world_root_snap']}")
    print()
    print(push_block(results_dir, rel_run_dir, env, world["scen"],
                     outcome["status"], outcome["final_level"]))


if __name__ == "__main__":
    main()
