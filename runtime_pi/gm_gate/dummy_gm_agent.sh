#!/bin/sh
# Dummy GM-scen agent (zero tokens): submit the fixed move in $HOME/move each
# wake, unless it is "skip". Ignores the payload; drains its inbox so files
# don't pile up. Stands in for a real Pi agent in the GM gate.
MOVE=$(cat "$HOME/move" 2>/dev/null)
[ "$MOVE" = "skip" ] || submit "$MOVE"
mkdir -p "$HOME/inbox_done" 2>/dev/null
mv "$HOME"/inbox/*.json "$HOME/inbox_done/" 2>/dev/null || true
exit 0
