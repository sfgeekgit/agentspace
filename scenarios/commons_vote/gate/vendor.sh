#!/bin/sh -e
# Vendor the pinned cilib subset into dispatch/ — byte-for-byte, list in dispatch/CILIB_PIN.
# Re-run after a pin bump, then re-run gate/offline_twin_equivalence.py.
SRC=/home/cc/cilib
DISPATCH="$(cd "$(dirname "$0")/../dispatch" && pwd)"

PIN=$(grep '^commit' "$DISPATCH/CILIB_PIN" | cut -d' ' -f3)
HEAD=$(git -C "$SRC" rev-parse HEAD)
[ "$PIN" = "$HEAD" ] || { echo "repo at $HEAD but CILIB_PIN says $PIN — checkout the pin or bump it"; exit 1; }

rm -rf "$DISPATCH/cilib" "$DISPATCH/experiments"
mkdir -p "$DISPATCH/experiments/basin_stability"
cp -r "$SRC/src/cilib" "$DISPATCH/cilib"
rm -f "$DISPATCH/cilib/core/simulation.py"   # unused here; the subset's sole pandas import
rm -rf "$DISPATCH/cilib/agents" "$DISPATCH/cilib/analysis" "$DISPATCH/cilib/environments" \
       "$DISPATCH/cilib/execution" "$DISPATCH/cilib/mechanisms" "$DISPATCH/cilib/paradigms" \
       "$DISPATCH/cilib/transformations"
for f in __init__ state transforms policies; do
  cp "$SRC/experiments/basin_stability/$f.py" "$DISPATCH/experiments/basin_stability/"
done
find "$DISPATCH/cilib" "$DISPATCH/experiments" -name __pycache__ -type d -prune -exec rm -rf {} +

# byte-for-byte verification against the pinned checkout (diff prints nothing)
diff -r -x __pycache__ -x simulation.py "$SRC/src/cilib/core" "$DISPATCH/cilib/core"
diff -r -x __pycache__ "$SRC/src/cilib/metrics" "$DISPATCH/cilib/metrics"
diff "$SRC/src/cilib/__init__.py" "$DISPATCH/cilib/__init__.py"
for f in __init__ state transforms policies; do
  diff "$SRC/experiments/basin_stability/$f.py" "$DISPATCH/experiments/basin_stability/$f.py"
done
echo "vendored OK from $SRC @ $HEAD"
