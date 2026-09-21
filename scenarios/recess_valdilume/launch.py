#!/usr/bin/env python3
"""Build and start Valdilume with an inexpensive all-DeepSeek roster.

Use --dry-run to inspect without Docker, credentials, writes or model calls.
Defaults apply here and persist in the built world; the shared wizard still
lets the operator choose any model. No global model defaults are changed.
"""
import argparse
import json
import math
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from agentspace import builder, registry

SCEN = "recess_valdilume"
# Lowest listed paid DeepSeek prompt AND completion prices in the public
# OpenRouter catalog checked 2026-09-20. Pin explicitly; never fall back to a
# more expensive family. Prices and availability can change after that date.
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env", nargs="?", default="recess_valdilume_run1")
    parser.add_argument("--world", default=SCEN)
    parser.add_argument("--agents", type=int, default=16)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--turns", type=int, default=80, choices=range(1, 81), metavar="1..80")
    parser.add_argument("--budget", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=92002)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not builder.valid_world_name(args.world) or not builder.valid_world_name(args.env):
        parser.error("world and environment names must be lowercase letters/digits/underscore")
    if not args.model.startswith("deepseek/"):
        parser.error("this launcher uses DeepSeek only; use the ordinary wizard for other providers")
    if not math.isfinite(args.budget) or args.budget <= 0:
        parser.error("budget must be a positive finite USD limit")
    scen = registry.load_scen(SCEN)
    if not scen["min_agents"] <= args.agents <= scen["max_agents"]:
        parser.error("agent count is outside the scenario limits")
    params = registry.validate_params(scen["params_schema"], {"max_turns": args.turns})
    try:
        seed, ids, roles = builder.plan_roster(SCEN, args.agents, params, seed=args.seed)
    except ValueError as exc:
        parser.error(str(exc))
    roster = builder.roster_for(roles, args.model, "blank")
    print(json.dumps({"world": args.world, "environment": args.env, "params": params,
                      "budget_usd": args.budget, "seed": seed,
                      "roster": [{"id": a, "role": r, **slot} for a, r, slot in zip(ids, roles, roster)]}, indent=2), flush=True)
    if args.dry_run:
        return
    # Load secrets through the same path as the normal CLI, never display them.
    import zookeeper  # noqa: F401
    from agentspace import db, snap
    if db.get_env(args.env) is not None:
        parser.error("that environment already exists; choose a new name or resume it with env kick")
    root = builder.cmd_build(SCEN, roster, world_name=args.world, params=params, seed=seed)
    snap.cmd_fork(f"{root['scenario']}:{root['version']}", args.env, budget_usd=args.budget, kick=True)


if __name__ == "__main__":
    main()
