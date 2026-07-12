#!/bin/sh -e
# Vendor the pinned cilib subset into gm/ — byte-for-byte, list in gm/CILIB_PIN.
# Re-run after a pin bump, then re-run gate/offline_twin_equivalence.py.
SRC=/home/cc/cilib
GM="$(cd "$(dirname "$0")/../gm" && pwd)"

PIN=$(grep '^commit' "$GM/CILIB_PIN" | cut -d' ' -f3)
HEAD=$(git -C "$SRC" rev-parse HEAD)
[ "$PIN" = "$HEAD" ] || { echo "repo at $HEAD but CILIB_PIN says $PIN — checkout the pin or bump it"; exit 1; }

rm -rf "$GM/cilib" "$GM/experiments"
mkdir -p "$GM/experiments/basin_stability"
cp -r "$SRC/src/cilib" "$GM/cilib"
rm -f "$GM/cilib/core/simulation.py"   # unused here; the subset's sole pandas import
rm -rf "$GM/cilib/agents" "$GM/cilib/analysis" "$GM/cilib/environments" \
       "$GM/cilib/execution" "$GM/cilib/mechanisms" "$GM/cilib/paradigms" \
       "$GM/cilib/transformations"
for f in __init__ state transforms policies; do
  cp "$SRC/experiments/basin_stability/$f.py" "$GM/experiments/basin_stability/"
done
find "$GM/cilib" "$GM/experiments" -name __pycache__ -type d -prune -exec rm -rf {} +

# byte-for-byte verification against the pinned checkout (diff prints nothing)
diff -r -x __pycache__ -x simulation.py "$SRC/src/cilib/core" "$GM/cilib/core"
diff -r -x __pycache__ "$SRC/src/cilib/metrics" "$GM/cilib/metrics"
diff "$SRC/src/cilib/__init__.py" "$GM/cilib/__init__.py"
for f in __init__ state transforms policies; do
  diff "$SRC/experiments/basin_stability/$f.py" "$GM/experiments/basin_stability/$f.py"
done
echo "vendored OK from $SRC @ $HEAD"
