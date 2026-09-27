#!/bin/bash
# Every 10 minutes: copy finished audit results into audit/second/raw/, commit, push.
# Stops when $SP/stop2 exists or after 8 hours.
SP=/tmp/claude-0/-home-user-reflecta-rps-7200/69e4bbe4-a038-5a75-8fd7-62a7b961a224/scratchpad
REPO=/home/user/reflecta-rps-7200
JOURNAL=/root/.claude/projects/-home-user-reflecta-rps-7200/69e4bbe4-a038-5a75-8fd7-62a7b961a224/subagents/workflows/wf_ea874017-29b/journal.jsonl
BRANCH=claude/clever-mayer-dy1j3o
cd "$REPO" || exit 1
end=$(( $(date +%s) + 24*3600 ))

push() {
  for d in 0 2 4 8 16; do
    sleep "$d"
    git push -u origin "$BRANCH" && return 0
  done
  return 1
}

while [ ! -e "$SP/stop2" ] && [ "$(date +%s)" -lt "$end" ]; do
  new=$(python3 "$SP/checkpoint.py" "$JOURNAL" "$REPO/audit/second/raw")
  if [ -n "$new" ]; then
    git add audit/second/raw/
    if ! git diff --cached --quiet -- audit/second/raw/; then
      git commit -q -m "Second audit checkpoint: $(echo $new | tr ' ' ',')

Raw results of the second read-only whole-code audit, saved as they finish so a
stopped session loses nothing already done.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EAW8sZ3KcvqTo8DaBRo3Mk" -- audit/second/raw/
      push && echo "$(date -u +%H:%M) pushed: $new" || echo "$(date -u +%H:%M) PUSH FAILED: $new"
    fi
  fi
  for i in $(seq 60); do [ -e "$SP/stop2" ] && break; sleep 10; done
done
echo "checkpoint loop ended $(date -u +%H:%M)"
