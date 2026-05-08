#!/bin/sh
# AMI installer — sets up the MCP server in ~/.ami so any agent can connect.
# Usage:
#   curl -fsSL https://ami-mock-api.onrender.com/install.sh | sh
#   AMI_HOME=/custom/path curl -fsSL https://... | sh
#
# Auto-wire into Claude Desktop's claude_desktop_config.json:
#   sh install.sh --wire
#   curl -fsSL https://... | sh -s -- --wire
#   AMI_WIRE_CLAUDE=1 curl -fsSL https://... | sh
# When run interactively (TTY) and a Claude config already exists, the
# installer will also prompt before wiring. AMI_API_KEY, if set in the
# environment, is baked into the merged config.

set -e

REPO="${AMI_REPO:-https://github.com/Gamino17/AMI}"
DEST="${AMI_HOME:-$HOME/.ami}"
API_URL="${AMI_API_URL:-https://ami-mock-api.onrender.com}"

# wire flag: --wire / --wire-claude or AMI_WIRE_CLAUDE=1
WIRE="${AMI_WIRE_CLAUDE:-0}"
for arg in "$@"; do
  case "$arg" in
    --wire|--wire-claude) WIRE=1 ;;
    --no-wire) WIRE=0 ;;
  esac
done

# colors (no-op on dumb terms)
if [ -t 1 ]; then
  C='\033[36m'; G='\033[32m'; Y='\033[33m'; R='\033[31m'; D='\033[2m'; X='\033[0m'
else
  C=''; G=''; Y=''; R=''; D=''; X=''
fi
say()  { printf "${C}%s${X}\n" "$1"; }
ok()   { printf "${G}%s${X}\n" "$1"; }
warn() { printf "${Y}%s${X}\n" "$1"; }
die()  { printf "${R}error:${X} %s\n" "$1" >&2; exit 1; }

cat <<EOF

   ${C}AMI${X} ${D}— Mobile identity for AI agents${X}
   ${D}reference implementation · v1.0${X}

EOF

# 1. dependencies
command -v git >/dev/null 2>&1 || die "git not found. Install git first: https://git-scm.com"

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver=$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")
    case "$ver" in 3.10|3.11|3.12|3.13|3.14|3.15)
      PY=$(command -v "$cand"); break ;;
    esac
  fi
done
[ -n "$PY" ] || die "Python 3.10+ not found. Install with: brew install python@3.13  (or use uv: brew install uv)"

say "Using Python: $PY ($($PY -c 'import sys; print(sys.version.split()[0])'))"

# 2. clone or update
if [ -d "$DEST/.git" ]; then
  say "Updating existing install at $DEST"
  git -C "$DEST" pull --quiet --ff-only || warn "git pull failed; keeping existing checkout"
else
  [ -e "$DEST" ] && die "$DEST exists and is not a git checkout. Set AMI_HOME or remove it."
  say "Cloning AMI into $DEST"
  git clone --quiet --depth 1 "$REPO" "$DEST"
fi

# 3. venv + deps
say "Setting up Python venv and installing dependencies"
if [ ! -d "$DEST/.venv" ]; then
  "$PY" -m venv "$DEST/.venv"
fi
"$DEST/.venv/bin/python" -m pip install --quiet --upgrade pip
"$DEST/.venv/bin/python" -m pip install --quiet -r "$DEST/requirements.txt"

# 4. quick sanity check
"$DEST/.venv/bin/python" -c "from mcp.server.fastmcp import FastMCP; import httpx" 2>/dev/null \
  || die "MCP SDK not importable after install"

ok "Installed at $DEST"

# 5. resolve Claude Desktop config path (per-OS)
CFG=""
case "$(uname -s)" in
  Darwin)
    CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json" ;;
  Linux)
    CFG="$HOME/.config/Claude/claude_desktop_config.json" ;;
  *)
    CFG="" ;;
esac

# 6. interactive prompt: if we have a TTY, the config dir exists, and wire
#    wasn't already requested, ask the user.
if [ "$WIRE" != "1" ] && [ -t 0 ] && [ -n "$CFG" ] && [ -f "$CFG" ]; then
  printf "${C}Wire AMI into Claude Desktop now?${X} [y/N] "
  read ans
  case "$ans" in
    y|Y|yes|YES|Yes) WIRE=1 ;;
  esac
fi

# 7. attempt the wire if requested
WIRED=0
WIRE_SKIP_REASON=""
if [ "$WIRE" = "1" ]; then
  if [ -z "$CFG" ]; then
    WIRE_SKIP_REASON="unsupported OS for auto-wire ($(uname -s))"
  elif [ ! -d "$(dirname "$CFG")" ]; then
    WIRE_SKIP_REASON="Claude Desktop not installed (missing $(dirname "$CFG"))"
  else
    # backup existing config (only if it exists)
    if [ -f "$CFG" ]; then
      TS=$(date +%Y%m%d-%H%M%S)
      BACKUP="$CFG.ami-backup-$TS"
      cp "$CFG" "$BACKUP" || die "failed to back up $CFG"
      say "Backed up existing config to $BACKUP"
    fi

    # merge using Python (already verified >=3.10 above)
    if "$PY" - "$DEST" "$API_URL" "$CFG" <<'PYEOF'
import json, sys, os
dest, api_url, cfg = sys.argv[1], sys.argv[2], sys.argv[3]
data = {}
if os.path.exists(cfg):
    try:
        with open(cfg) as f:
            data = json.load(f)
    except Exception as e:
        sys.exit(f"failed to parse existing config: {e}")
if not isinstance(data, dict):
    sys.exit("existing config is not a JSON object")
servers = data.setdefault("mcpServers", {})
if not isinstance(servers, dict):
    sys.exit("existing mcpServers is not a JSON object")
servers["ami"] = {
    "command": f"{dest}/.venv/bin/python",
    "args": [f"{dest}/ami_mcp.py"],
    "env": {
        "AMI_API_URL": api_url,
        "AMI_API_KEY": os.environ.get("AMI_API_KEY", "<your-api-key>"),
    },
}
os.makedirs(os.path.dirname(cfg), exist_ok=True)
with open(cfg, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"wired into {cfg}")
PYEOF
    then
      WIRED=1
    else
      warn "auto-wire failed; falling back to manual snippet"
    fi
  fi
fi

# 8. output: either wired confirmation or manual snippet
if [ "$WIRED" = "1" ]; then
  ok "Wired into $CFG"
  if [ -z "${AMI_API_KEY:-}" ]; then
    warn "AMI_API_KEY was not set — placeholder '<your-api-key>' written; edit $CFG before launching Claude."
  fi
  cat <<EOF

${C}Next:${X} restart Claude Desktop to load the ${G}ami.*${X} tools.

${D}Docs:    $API_URL${X}
${D}Repo:    $REPO${X}
${D}For agents: $API_URL/llms.txt${X}

EOF
else
  if [ "$WIRE" = "1" ] && [ -n "$WIRE_SKIP_REASON" ]; then
    warn "Skipped auto-wire: $WIRE_SKIP_REASON"
  fi
  cat <<EOF

${C}Connect Claude Desktop / Code (stdio)${X}
${D}Add this to your claude_desktop_config.json and restart the client:${X}

  "mcpServers": {
    "ami": {
      "command": "$DEST/.venv/bin/python",
      "args": ["$DEST/ami_mcp.py"],
      "env": {
        "AMI_API_URL": "$API_URL",
        "AMI_API_KEY": "<your-api-key>"
      }
    }
  }

${C}Or run the MCP HTTP server locally${X}
  AMI_API_URL=$API_URL AMI_API_KEY=<key> $DEST/.venv/bin/python $DEST/ami_mcp.py http

${C}Smoke test against the public API${X}
  AMI_API_URL=$API_URL AMI_API_KEY=<key> python3 $DEST/demo_flow.py

${D}Tip: re-run with --wire to auto-configure Claude Desktop:${X}
  ${D}curl -fsSL $API_URL/install.sh | sh -s -- --wire${X}

${D}Docs:    $API_URL${X}
${D}Repo:    $REPO${X}
${D}For agents: $API_URL/llms.txt${X}

EOF
fi

ok "Done."
