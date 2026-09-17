#!/bin/sh
# Scripted gate dummy: consume ONE line of $HOME/moves per wake and run its
# ;-separated actions: `post <text>` | `send <to> <text>` | `submit <x>`
# (anything else = no-op). Positional lines make whole scripted multi-phase
# runs deterministic. Gate/policy denials are expected outcomes, hence `|| true`.
LINE=$(head -n1 "$HOME/moves" 2>/dev/null)
sed -i 1d "$HOME/moves" 2>/dev/null
IFS=';'; set -- $LINE; unset IFS   # split into commands, then restore normal splitting
for CMD do
    CMD=${CMD# }
    case "$CMD" in
        post\ *)   gateway post "${CMD#post }" || true ;;
        send\ *)   REST=${CMD#send }; TO=${REST%% *}; gateway send "$TO" "${REST#* }" || true ;;
        submit\ *) submit "${CMD#submit }" ;;
    esac
done
mkdir -p "$HOME/inbox_done" 2>/dev/null
mv "$HOME"/inbox/*.json "$HOME/inbox_done/" 2>/dev/null || true
exit 0
