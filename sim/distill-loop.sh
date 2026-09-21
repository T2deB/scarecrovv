#!/bin/zsh
# Iterated policy distillation, unattended.
#
#   corpus from teacher T  ->  fit student S  ->  validate S  ->  T := S  ->  repeat
#
# This is the loop that makes distillation more than a one-off: the search using
# S is stronger than S alone, so round N+1's corpus contains better decisions
# than round N's student knows. The improvement operator has to be stronger than
# what it improves, which is why the teacher is always search(T) and never T.
#
# RESUMABLE, and deliberately NOT by "does the output file exist" -- that is the
# bug that made the weekend report a dry run's numbers as results. Each stage
# drops an explicit .done marker, and every artefact is named by round.
set -u
cd "$(dirname "$0")"
R=./register.mjs
ROUNDS=${ROUNDS:-2}
SHARDS=${SHARDS:-4}
PER=${PER:-125}          # games per shard per round
SEEDS=${SEEDS:-30}       # paired seeds per validation match
mkdir -p wk
say() { print -r -- "=== $(date '+%H:%M:%S') $*"; }
stage() { [[ -f "wk/$1.done" ]] }
mark()  { : > "wk/$1.done" }

TEACHER=hyb-animals.json

for n in $(seq 1 $ROUNDS); do
  say "ROUND $n — teacher: $TEACHER"

  if stage "corpus$n"; then
    say "round $n corpus: already complete"
  else
    for i in $(seq 0 $((SHARDS-1))); do
      caffeinate -i node --max-old-space-size=4096 --import $R distill.ts "$TEACHER" $PER \
        --from=$((n * 1000000 + i * 10000)) --out=wk/d${n}_$i.jsonl >> wk/d${n}_$i.log 2>&1 &
    done
    wait
    cat wk/d${n}_*.jsonl > wk/corpus$n.jsonl
    mark "corpus$n"
  fi
  say "round $n corpus: $(grep -c . wk/corpus$n.jsonl | tr -d ' ') games"

  if stage "fit$n"; then
    say "round $n fit: already complete"
  else
    node --import $R distilfit.ts wk/corpus$n.jsonl --out=wk/distilled$n.json \
      --ref=$TEACHER --iters=600 > wk/fit$n.log 2>&1
    mark "fit$n"
  fi
  tail -28 wk/fit$n.log

  # Against the round's own teacher (did distillation add anything?) and against
  # the standing champion (is it actually good?).
  # basename: a teacher path like wk/distilled1.json used to put a slash in the
  # tag, so the redirect wrote to a directory that does not exist and the
  # round-vs-its-own-teacher match -- the one the promotion gate needs -- was
  # silently skipped.
  for opp in "$TEACHER" hyb-animals.json scarecrovv-hunter; do
    base="${opp##*/}"; tag="r${n}-vs-${base%%.json}"
    if stage "m-$tag"; then
      print -r -- "--- $tag (already done)"
    else
      print -r -- "--- $tag"
      node --import $R match.ts wk/distilled$n.json "$opp" $SEEDS --search \
        --offset=$((600000 + n * 1000)) > "wk/match-$tag.txt" 2>&1
      mark "m-$tag"
    fi
    tail -6 "wk/match-$tag.txt"
  done

  # PROMOTION GATE. Only hand the student the teacher's job if it BEAT the
  # teacher with an interval clear of zero.
  #
  # Round 2 of the first run is why this exists. Round 1's student came out
  # level with the incumbent (+2.27, interval spanning zero), so search(student)
  # was no stronger than search(teacher) and round 2 had nothing to learn from
  # -- it inherited drift instead and lost to its own teacher by 17.85 trail.
  #
  # Its top-1 agreement went UP while it did so, 63.0% to 65.7%, because top-1
  # measures fidelity to whatever generated the corpus. A better imitator of a
  # worse teacher scores higher on it. Never gate on top-1; gate on a match.
  own="wk/match-r${n}-vs-$(basename ${TEACHER%%.json}).txt"
  if [[ -s $own ]] && grep -q "^  distilled$n.json .*interval excludes zero" $own; then
    say "round $n: student beat its teacher — promoting"
    TEACHER=wk/distilled$n.json
  else
    say "round $n: student did NOT beat its teacher — stopping. wk/distilled$n.json is the result."
    break
  fi
done
say "all rounds done — artefacts in sim/wk/"
