#!/usr/bin/env bash
# WHY:  single, safe entry point to put the second-brain plugin on a machine.
# WHAT: (1) copies skills/brain to $CLAUDE_CONFIG_DIR/skills/brain (code: always overwritten,
#       but refuses to clobber a foreign dir unless BRAIN_FORCE=1), (2) merges the hooks from
#       hooks/brain.json into settings.json idempotently, (3) scaffolds the store
#       $AGENT_HOME/brain only if it is missing (data: never touched afterwards).
# WHO:  a human, once per machine and again after every change to this repo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_HOME="${AGENT_HOME:-$HOME/.agents}"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STORE_ROOT="$AGENT_HOME/brain"
SRC_SKILL="$SCRIPT_DIR/skills/brain"
DEST_SKILL="$CLAUDE_DIR/skills/brain"
SETTINGS="$CLAUDE_DIR/settings.json"
HOOKS_SRC="$SCRIPT_DIR/hooks/brain.json"
TMP_SKILL=""

die() { echo "error: $*" >&2; exit 1; }
cleanup() { [ -n "$TMP_SKILL" ] && [ -d "$TMP_SKILL" ] && rm -rf "$TMP_SKILL"; return 0; }
trap cleanup EXIT

is_ours() { [ -f "$1/scripts/brain_cli.py" ] && [ -f "$1/.brain-plugin" ]; }

command -v python3 >/dev/null 2>&1 || die "python3 is required"
[ -f "$SRC_SKILL/.brain-plugin" ] && [ -f "$SRC_SKILL/scripts/brain_cli.py" ] || die "incomplete source tree: $SRC_SKILL"
[ -f "$HOOKS_SRC" ] || die "missing $HOOKS_SRC"
case "$DEST_SKILL" in *\"*) die "skill path must not contain a double quote: $DEST_SKILL" ;; esac

# --- 1. skill ---------------------------------------------------------------
if [ -L "$DEST_SKILL" ] || [ -e "$DEST_SKILL" ]; then
  if ! is_ours "$DEST_SKILL" && [ "${BRAIN_FORCE:-}" != "1" ]; then
    die "$DEST_SKILL exists and is not this plugin (no scripts/brain_cli.py + .brain-plugin). Refusing to overwrite; set BRAIN_FORCE=1 to replace it."
  fi
fi
mkdir -p "$CLAUDE_DIR/skills"
TMP_SKILL="$(mktemp -d "$CLAUDE_DIR/skills/.brain.new.XXXXXX")"
cp -R "$SRC_SKILL/." "$TMP_SKILL/"
find "$TMP_SKILL" \( -name '__pycache__' -o -name '.DS_Store' \) -prune -exec rm -rf {} +
if [ -d "$TMP_SKILL/scripts" ]; then
  find "$TMP_SKILL/scripts" -type f -name '*.py' -exec chmod +x {} +
fi
if [ -L "$DEST_SKILL" ]; then
  rm -f "$DEST_SKILL"            # remove the link itself, never its target
elif [ -d "$DEST_SKILL" ]; then
  rm -rf "$DEST_SKILL"
elif [ -e "$DEST_SKILL" ]; then
  rm -f "$DEST_SKILL"
fi
mv "$TMP_SKILL" "$DEST_SKILL"
TMP_SKILL=""
chmod 755 "$DEST_SKILL"
echo "skill: installed $DEST_SKILL"

# --- 2. hooks ---------------------------------------------------------------
python3 - "$SETTINGS" "$HOOKS_SRC" "$DEST_SKILL" <<'PY'
import json, os, shutil, sys, tempfile

settings_p, tmpl_p, skill_dir = sys.argv[1:4]

def subst(o):
    if isinstance(o, str):
        return o.replace("{{SKILL_DIR}}", skill_dir)
    if isinstance(o, list):
        return [subst(x) for x in o]
    if isinstance(o, dict):
        return {k: subst(v) for k, v in o.items()}
    return o

def fail(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)

try:
    with open(tmpl_p, encoding="utf-8") as fh:
        ours = subst(json.load(fh)).get("hooks", {})
except (OSError, ValueError) as exc:
    fail("cannot read hook template: %s" % exc)

existed = os.path.exists(settings_p)
settings = {}
if existed:
    try:
        with open(settings_p, encoding="utf-8") as fh:
            settings = json.load(fh)
    except (OSError, ValueError) as exc:
        fail("%s is not valid JSON (%s); fix it first, nothing was changed" % (settings_p, exc))
    if not isinstance(settings, dict):
        fail("%s is not a JSON object; nothing was changed" % settings_p)
before = json.dumps(settings, sort_keys=True)

hooks = settings.setdefault("hooks", {})
if not isinstance(hooks, dict):
    fail("'hooks' in %s is not an object; nothing was changed" % settings_p)
marker = skill_dir + "/scripts/session_"

def is_ours(h):
    return isinstance(h, dict) and marker in str(h.get("command", ""))

for event, new_entries in ours.items():
    cur = hooks.get(event, [])
    if not isinstance(cur, list):
        fail("hooks.%s in %s is not a list; nothing was changed" % (event, settings_p))
    kept = []
    for entry in cur:
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            rest = [h for h in entry["hooks"] if not is_ours(h)]
            if len(rest) != len(entry["hooks"]):
                if rest:
                    entry = dict(entry, hooks=rest)
                else:
                    continue
        kept.append(entry)
    hooks[event] = kept + new_entries

if json.dumps(settings, sort_keys=True) == before and existed:
    print("hooks: already wired in %s" % settings_p)
    sys.exit(0)

os.makedirs(os.path.dirname(settings_p) or ".", exist_ok=True)
if existed and not os.path.exists(settings_p + ".bak"):
    shutil.copy2(settings_p, settings_p + ".bak")
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(settings_p) or ".", prefix=".settings.", suffix=".tmp")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    if existed:
        shutil.copymode(settings_p, tmp)
    os.replace(tmp, settings_p)
except BaseException:
    if os.path.exists(tmp):
        os.unlink(tmp)
    raise
print("hooks: wired SessionStart + SessionEnd in %s" % settings_p)
PY

# --- 3. store ---------------------------------------------------------------
if [ -e "$STORE_ROOT" ]; then
  echo "store: $STORE_ROOT already exists, leaving it untouched"
else
  mkdir -p "$STORE_ROOT/inbox" "$STORE_ROOT/long-memory"
  if [ -f "$SRC_SKILL/config/state.json" ]; then
    cp "$SRC_SKILL/config/state.json" "$STORE_ROOT/state.json"
  else
    printf '{\n  "enabled": true,\n  "digest_model": "auto"\n}\n' > "$STORE_ROOT/state.json"
  fi
  : > "$STORE_ROOT/.sessions"
  : > "$STORE_ROOT/short-memory.md"
  for f in index log; do
    printf -- '---\nokf_version: "0.1"\ngenerated: { by: process:install, at: 1970-01-01T00:00:00Z }\n---\n' \
      > "$STORE_ROOT/long-memory/$f.md"
  done
  NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"ts": "%s", "short_lines": 0, "long_pages": 0, "long_bytes": 0, "tags": 0}\n' "$NOW" \
    > "$STORE_ROOT/metrics.jsonl"
  printf '%s install created store\n' "$(date +%Y-%m-%dT%H:%M:%S%z)" > "$STORE_ROOT/brain.log"
  echo "store: created $STORE_ROOT"
fi

echo "done. Restart Claude Code for the hooks to take effect; run: python3 $DEST_SKILL/scripts/brain_cli.py doctor"
