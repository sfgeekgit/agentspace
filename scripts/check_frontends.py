#!/usr/bin/env python3
"""Every library verb (agentspace/*.py `def cmd_*`) must be reachable from BOTH
front ends in zookeeper.py: the click commands and the interactive menu; and
every click leaf must have a web form (web.py) or a web.SPECIAL entry.
Exit 1 and name the gaps otherwise. Zero deps, instant."""
import re, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
cli, menu = (root / "zookeeper.py").read_text().split("# INTERACTIVE MENU", 1)
gaps = []
for f in sorted((root / "agentspace").glob("*.py")):
    for fn in re.findall(r"^def (cmd_\w+)", f.read_text(), re.M):
        pat = re.compile(rf"\b{f.stem}(_mod)?\.{fn}\(")
        for side, text in (("cli", cli), ("menu", menu)):
            if not pat.search(text):
                gaps.append(f"{f.stem}.{fn} missing from {side}")
# Web: every non-SPECIAL leaf must render a form (a new param shape trips ValueError).
sys.path.insert(0, str(root)); import web, zookeeper          # noqa: E402
leaves = {" ".join(p): c for p, c in web.leaf_commands(zookeeper.cli)}
gaps += [f"web.SPECIAL names no click command: {k}" for k in web.SPECIAL if k not in leaves]
for key, cmd in leaves.items():
    try:
        key in web.SPECIAL or web.form_html(tuple(key.split()), cmd)
    except ValueError as e:
        gaps.append(f"{key}: web form: {e}")
print("\n".join(gaps) or "front ends in sync")
sys.exit(1 if gaps else 0)
