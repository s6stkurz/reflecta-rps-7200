#!/bin/bash
# Every 10 minutes: copy finished agent results of every journal listed in
# journals.txt ("<journal> <repo-relative out dir>" per line) into the repo,
# commit and push. Stops when $SP/stop3 exists or after 24 hours.
SP=/tmp/claude-0/-home-user-reflecta-rps-7200/69e4bbe4-a038-5a75-8fd7-62a7b961a224/scratchpad
REPO=/home/user/reflecta-rps-7200
BRANCH=claude/clever-mayer-dy1j3o
cd "$REPO" || exit 1
end=$(( $(date +%s) + 24*3600 ))
push() {
  for d in 0 2 4 8 16; do sleep "$d"; git push -u origin "$BRANCH" && return 0; done
  return 1
}
while [ ! -e "$SP/stop3" ] && [ "$(date +%s)" -lt "$end" ]; do
  new=""; dirs=""
  while read -r journal out; do
    [ -f "$journal" ] || continue
    got=$(python3 "$SP/checkpoint.py" "$journal" "$REPO/$out")
    [ -n "$got" ] && new="$new $got" && dirs="$dirs $out"
  done < "$SP/journals.txt"
  if [ -n "$new" ]; then
    git add $dirs
    if ! git diff --cached --quiet -- $dirs; then
      git commit -q -m "Audit run checkpoint:$(echo $new | tr ' ' ',' | sed 's/^/ /')

Agent results saved as they finish, so a run a usage limit stops loses
nothing already done.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EAW8sZ3KcvqTo8DaBRo3Mk" -- $dirs
      push && echo "$(date -u +%H:%M) pushed:$new" || echo "$(date -u +%H:%M) PUSH FAILED:$new"
    fi
  fi
  for i in $(seq 60); do [ -e "$SP/stop3" ] && break; sleep 10; done
done
echo "checkpoint loop ended $(date -u +%H:%M)"
