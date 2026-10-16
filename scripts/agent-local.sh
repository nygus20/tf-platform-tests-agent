#!/usr/bin/env bash
# Run the Terraform test agent locally exactly as CI does: same work directory,
# same flags, same guard, same test runner. Nothing in your checkout changes
# unless you pass --apply.
#
# Usage: scripts/agent-local.sh [--base <ref>] [--backfill] [--apply] [--skip-tests]
#
# Needs: git, python 3.11+ with jsonschema (or the repo .venv), node 22+ and npm,
# terraform 1.16.6 on PATH (or TERRAFORM=/path/to/terraform), and credentials:
# OPENROUTER_API_KEY in the environment uses OpenRouter, as CI does when that
# secret is set; otherwise ANTHROPIC_API_KEY or your Claude Code login is used.
# Set CLAUDE_BIN to use a different claude binary.
set -euo pipefail

base="origin/main"
backfill=()
apply=0
skip_tests=0
while [ $# -gt 0 ]; do
  case "$1" in
    --base) base="$2"; shift 2 ;;
    --backfill) backfill=(--backfill); shift ;;
    --apply) apply=1; shift ;;
    --skip-tests) skip_tests=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

root="$(git rev-parse --show-toplevel)"
cd "$root"
tf="${TERRAFORM:-terraform}"

if [ -x "$root/.venv/Scripts/python.exe" ]; then py="$root/.venv/Scripts/python.exe"
elif [ -x "$root/.venv/bin/python" ]; then py="$root/.venv/bin/python"
else py="python3"; fi

# Paths handed to a native Windows python or claude must be Windows paths.
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

if [ -z "${CLAUDE_BIN:-}" ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) plat="linux-x64"; exe="claude" ;;
    Linux-aarch64) plat="linux-arm64"; exe="claude" ;;
    Darwin-arm64) plat="darwin-arm64"; exe="claude" ;;
    Darwin-x86_64) plat="darwin-x64"; exe="claude" ;;
    MINGW*|MSYS*|CYGWIN*) plat="win32-x64"; exe="claude.exe" ;;
    *) echo "unsupported platform; set CLAUDE_BIN" >&2; exit 2 ;;
  esac
  CLAUDE_BIN="$root/.github/agent-runtime/node_modules/@anthropic-ai/claude-code-$plat/$exe"
  if [ ! -x "$CLAUDE_BIN" ]; then
    (cd "$root/.github/agent-runtime" && npm ci --ignore-scripts --no-audit --no-fund >/dev/null)
  fi
fi
echo "claude: $("$CLAUDE_BIN" --version)"

run_dir="$(mktemp -d "${TMPDIR:-/tmp}/tf-agent.XXXXXX")"
echo "run directory: $run_dir"

# 1. detect
"$py" .github/scripts/detect_changes.py --git-base "$base" --root "$(native "$root")" \
  "${backfill[@]}" --out-dir "$(native "$run_dir/changes")"

# 2. work directory, built the same way as the CI agent job
work="$run_dir/work"
mkdir -p "$work"
git archive HEAD modules stacks README.md | tar -x -C "$work"
find "$work" \( -name CLAUDE.md -o -name CLAUDE.local.md -o -name AGENTS.md -o -name .mcp.json \) -type f -delete
find "$work" -name .claude -type d -prune -exec rm -rf {} +
find "$work" -type l -delete
mkdir -p "$work/.claude" "$work/.agent"
cp -R .claude/agents .claude/skills "$work/.claude/"
cp .github/schemas/report.schema.json "$work/.claude/skills/tf-agent-report/"
cp "$run_dir/changes/changes.json" "$run_dir/changes/changes.patch" "$work/.agent/"
git -C "$work" init -q
git -C "$work" config core.autocrlf false
git -C "$work" add -A
git -C "$work" -c user.name=agent -c user.email=agent@localhost -c core.autocrlf=false commit -qm baseline

# 3. agent, same flags as CI
(
  cd "$work"
  export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1 \
         CLAUDE_CODE_DISABLE_CLAUDE_MDS=1 CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
         CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1
  # Same provider choice and model pins as CI.
  if [ -n "${OPENROUTER_API_KEY:-}" ]; then
    export ANTHROPIC_BASE_URL="https://openrouter.ai/api"
    export ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY"
    export ANTHROPIC_API_KEY=""
    export ANTHROPIC_DEFAULT_SONNET_MODEL="anthropic/claude-sonnet-5.5[1m]"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="anthropic/claude-opus-5.5[1m]"
    echo openrouter > "$run_dir/provider.txt"
  else
    export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-5-5"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-5-5"
    echo anthropic > "$run_dir/provider.txt"
  fi
  unset OPENROUTER_API_KEY
  set +e
  "$CLAUDE_BIN" -p "Run the Terraform test procedure for the modules in .agent/changes.json." \
    --agent tf-test-agent \
    --setting-sources project \
    --settings "$(native "$root/.claude/settings.ci.json")" \
    --permission-mode dontAsk \
    --tools "Read,Glob,Grep,Write,Edit,Agent,Skill" \
    --disallowedTools "Bash,WebFetch,WebSearch,NotebookEdit,mcp__*" \
    --strict-mcp-config --mcp-config "$(native "$root/.claude/mcp-empty.json")" \
    --max-turns 60 \
    --max-budget-usd 3 \
    --output-format stream-json --verbose \
    < /dev/null > "$run_dir/transcript.jsonl"
  echo "claude exit code: $?"
)

# 4. collect, same as CI
out="$run_dir/agent-output"
mkdir -p "$out/files"
(
  cd "$work"
  git add -A
  git diff --cached --name-status --no-renames -- . ':(exclude).agent/report.json' > "$out/changes.tsv"
  git diff --cached --name-only --no-renames --diff-filter=AM -z -- . ':(exclude).agent/report.json' |
    while IFS= read -r -d '' f; do mkdir -p "$out/files/$(dirname "$f")"; cp -P "$f" "$out/files/$f"; done
  if [ -f .agent/report.json ]; then cp .agent/report.json "$out/report.json"; fi
)
cp "$run_dir/transcript.jsonl" "$run_dir/provider.txt" "$out/"
"$py" - "$(native "$out/transcript.jsonl")" "$(native "$out/result.json")" <<'PY'
import json, sys
last = None
for line in open(sys.argv[1], encoding="utf-8"):
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    if ev.get("type") == "result":
        last = ev
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(last) if last else "")
PY
echo "agent changed:"; cat "$out/changes.tsv"

# 5. guard
set +e
"$py" .github/scripts/guard.py --changes "$(native "$run_dir/changes/changes.json")" \
  --agent-dir "$(native "$out")" --head "$(native "$root")" \
  --schema "$(native "$root/.github/schemas/report.schema.json")" --out-dir "$(native "$run_dir/guard")"
guard_rc=$?
set -e

# 6. terraform test on a clean copy of HEAD plus the accepted files
results_args=()
if [ "$skip_tests" -eq 0 ]; then
  head_copy="$run_dir/head"
  mkdir -p "$head_copy"
  git archive HEAD | tar -x -C "$head_copy"
  overlay=()
  if [ "$guard_rc" -eq 0 ] && [ -d "$run_dir/guard/accepted" ]; then overlay=(--overlay "$(native "$run_dir/guard/accepted")"); fi
  set +e
  "$py" .github/scripts/run_tests.py --root "$(native "$head_copy")" \
    --changes "$(native "$run_dir/changes/changes.json")" "${overlay[@]}" --terraform "$tf" \
    --out "$(native "$run_dir/results/results.json")" --formatted-dir "$(native "$run_dir/results/formatted")"
  set -e
  results_args=(--results "$(native "$run_dir/results/results.json")")
fi

# 7. summary, rendered exactly as the PR comment would be
"$py" .github/scripts/render_summary.py "${results_args[@]}" \
  --guard "$(native "$run_dir/guard/guard.json")" --report "$(native "$run_dir/guard/report.json")" \
  --sha "$(git rev-parse HEAD)" --out "$(native "$run_dir/summary.md")"
echo
cat "$run_dir/summary.md"

if [ "$apply" -eq 1 ] && [ -d "$run_dir/results/formatted/modules" ]; then
  cp -R "$run_dir/results/formatted/modules/." "$root/modules/"
  echo "Applied the accepted, formatted test files to your working tree. Review and commit them."
fi
echo "Everything from this run is in $run_dir"
