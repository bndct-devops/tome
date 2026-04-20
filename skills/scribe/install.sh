#!/usr/bin/env bash
# install.sh — symlink tome/skills/scribe into ~/.claude/skills/scribe
# Idempotent.  Safe to re-run.
set -euo pipefail

# Resolve the directory that contains this script (the scribe skill dir)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_HOME="$HOME/.claude/skills"
LINK="$SKILLS_HOME/scribe"

echo "Scribe install"
echo "  source : $SCRIPT_DIR"
echo "  target : $LINK"
echo ""

# 1. Create ~/.claude/skills/ if needed
if [[ ! -d "$SKILLS_HOME" ]]; then
    mkdir -p "$SKILLS_HOME"
    echo "Created $SKILLS_HOME"
fi

# 2. Handle existing target
if [[ -e "$LINK" || -L "$LINK" ]]; then
    if [[ -L "$LINK" ]]; then
        # It's already a symlink — re-point it regardless of where it points
        rm "$LINK"
        echo "Removed existing symlink at $LINK"
    else
        # It's a real directory — warn and abort
        echo "ERROR: $LINK exists and is a real directory (not a symlink)."
        echo "Remove it manually if you want to replace it:"
        echo "  rm -rf \"$LINK\""
        exit 1
    fi
fi

# 3. Create the symlink
ln -s "$SCRIPT_DIR" "$LINK"
echo "Linked $LINK -> $SCRIPT_DIR"

# 4. Done
echo ""
echo "Scribe is installed."
echo ""
echo "Next steps:"
echo "  1. Open any Claude Code session."
echo "  2. Run:  /scribe <path-to-folder>"
echo "     or just say:  \"import these books into Tome\""
echo "  3. First run will ask for your Tome URL and API token."
echo "     Create a token at:  Tome -> Settings -> API Tokens"
echo "     Name it something like \"scribe-laptop\"."
