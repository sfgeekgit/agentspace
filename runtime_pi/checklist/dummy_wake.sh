#!/bin/bash
# Step-1 dummy agent (zero tokens). Installed as /agents/<id>/on_wake and
# spawned BY THE GATEWAY as this agent's own linux user.
#
# Behavior: log every inbox message; reply exactly once to messages whose
# text contains "please-reply"; otherwise stay silent — the no-reply norm,
# scripted. Also detects serialization violations: if another on_wake for
# this agent is already running, log OVERLAP.
LOG="$HOME/log.txt"

if [ -e "$HOME/.wake_running" ]; then
    echo "OVERLAP $(date +%s.%N)" >> "$LOG"
fi
touch "$HOME/.wake_running"
sleep 0.3   # widen the window so a serialization bug would actually overlap

mkdir -p "$HOME/inbox_done"
for f in $(ls "$HOME"/inbox/*.json 2>/dev/null | sort); do
    frm=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["from"])' "$f")
    txt=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["text"])' "$f")
    echo "RECV from=$frm text=$txt" >> "$LOG"
    case "$txt" in
        *please-reply*)
            python3 /runtime_pi/pi_gateway_client.py send "$frm" "reply-from-$AGENT_ID" >> "$LOG" 2>&1
            ;;
    esac
    mv "$f" "$HOME/inbox_done/"
done

rm -f "$HOME/.wake_running"
