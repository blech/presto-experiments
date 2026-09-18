#!/usr/bin/env bash
#
# Drive prestoradar/dev/font_probe.py across its whole (display config x text
# backend x font) matrix, one step per fresh VM, and print a table.
#
#   prestoradar/dev/font_probe.sh
#
# Each step runs in its own process and, when it's recorded its result,
# hard-resets the board itself (Presto(full_res=True) leaves this firmware
# unable to soft-reset -- see font_probe.py's docstring).  This script runs
# a step, waits for the board to re-enumerate, and moves on.  Results
# accumulate in /font_probe.results ON THE BOARD, so if a step hangs the
# RP2350 outright (a bad .af at full_res does this, before the self-reset is
# reached) you can press RESET and re-run this script -- it skips steps
# that already have a result line and carries on.
#
# "OK" in the table means the step drew without an exception.  It does NOT
# mean the glyphs were legible -- watch the screen (each frame is labelled
# with its step number and config).  Add --hold to pause after each OK step.
#
# Env:
#   MPREMOTE   path to mpremote           (default: ../venv/bin/mpremote)
#   STEP_TIMEOUT  seconds per step        (default: 40)
#   FROM / TO  step range to run          (default: 0 .. count-1)

set -uo pipefail
cd "$(dirname "$0")"

MPREMOTE="${MPREMOTE:-../../venv/bin/mpremote}"
[ -x "$MPREMOTE" ] || MPREMOTE="../../../venv/bin/mpremote"
[ -x "$MPREMOTE" ] || MPREMOTE="$(command -v mpremote || true)"
STEP_TIMEOUT="${STEP_TIMEOUT:-40}"
HOLD=0
FRESH=0
for a in "$@"; do
  case "$a" in
    --hold)  HOLD=1 ;;
    --fresh) FRESH=1 ;;
  esac
done

if [ -z "${MPREMOTE:-}" ] || ! command -v "$MPREMOTE" >/dev/null 2>&1 && [ ! -x "$MPREMOTE" ]; then
  echo "error: mpremote not found (set MPREMOTE=...)" >&2
  exit 1
fi

# macOS `timeout` is `gtimeout` from coreutils; fall back to a perl shim.
if command -v timeout >/dev/null 2>&1; then TIMEOUT="timeout"
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT="gtimeout"
else TIMEOUT=""; fi
run_to() {  # run_to <seconds> <cmd...>
  local s="$1"; shift
  if [ -n "$TIMEOUT" ]; then "$TIMEOUT" "$s" "$@"; else
    perl -e 'my $p=fork; if(!$p){exec @ARGV or exit 127} $SIG{ALRM}=sub{kill 9,$p;exit 124}; alarm shift; waitpid $p,0; exit($?>>8)' "$s" "$@"
  fi
}

echo "mpremote: $MPREMOTE"
echo "copying font_probe.py to the board ..."
"$MPREMOTE" cp font_probe.py :font_probe.py || { echo "cp failed" >&2; exit 1; }

if [ "$FRESH" -eq 1 ]; then
  echo "wiping /font_probe.results ..."
  "$MPREMOTE" exec 'import font_probe; font_probe.reset_results()' 2>&1 | sed 's/^/  /'
fi

COUNT="$("$MPREMOTE" exec 'import font_probe; print(font_probe.count())' 2>/dev/null | tr -dc 0-9)"
[ -n "$COUNT" ] || { echo "could not get step count from board" >&2; exit 1; }
FROM="${FROM:-0}"
TO="${TO:-$((COUNT - 1))}"
echo "matrix has $COUNT steps; running $FROM..$TO (${STEP_TIMEOUT}s each)"
echo

# steps that already have any result line on the board -> skip on a re-run
DONE_STEPS="$("$MPREMOTE" exec '
try:
    seen=set()
    for ln in open("/font_probe.results"):
        p=ln.split("|",1)
        if p and p[0].strip().isdigit(): seen.add(int(p[0]))
    print(",".join(str(x) for x in sorted(seen)))
except OSError:
    print("")
' 2>/dev/null | tr -dc '0-9,')"
echo "already have results for steps: [${DONE_STEPS:-none}]"
echo

wait_for_board() {  # poll until the raw REPL answers again, up to ~30s
  local i
  for ((i = 0; i < 15; i++)); do
    sleep 2
    run_to 8 "$MPREMOTE" exec "pass" >/dev/null 2>&1 && return 0
  done
  return 1
}

for ((n = FROM; n <= TO; n++)); do
  case ",$DONE_STEPS," in
    *",$n,"*) echo "step $n: skip (already recorded)"; continue ;;
  esac
  printf 'step %d/%d ... \n' "$n" "$((COUNT - 1))"
  # The step self-hard-resets when done, so mpremote will usually lose the
  # connection mid-call -- a non-zero RC here is expected, not a failure.
  OUT="$(run_to "$STEP_TIMEOUT" "$MPREMOTE" exec "import font_probe; font_probe.main($n)" 2>&1)"
  echo "$OUT" | grep -E '^\|?[0-9]+\|' | sed 's/^/    /'

  [ "$HOLD" -eq 1 ] && read -r -p "    [enter] once you've looked at the screen " _

  if ! wait_for_board; then
    echo
    echo "================================================================"
    echo " Board did not come back after step $n (raw REPL unreachable)."
    echo " That step hung the RP2350 before it could self-reset."
    echo " Press RESET on the Presto, then re-run this script -- it"
    echo " skips step $n (already has a START line) and continues."
    echo "================================================================"
    exit 2
  fi
done

echo
echo "==================== SUMMARY ===================="
"$MPREMOTE" exec "import font_probe; font_probe.summary()" 2>&1
