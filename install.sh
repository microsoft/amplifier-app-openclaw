#!/usr/bin/env bash
# install.sh — One-command installer for amplifier-openclaw
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/bkrabach/amplifier-app-openclaw/main/install.sh | bash
#
# Prerequisites: none (installs uv if missing)
# Result: amplifier-openclaw binary on PATH

set -euo pipefail

REPO="https://github.com/bkrabach/amplifier-app-openclaw"
PACKAGE="amplifier-app-openclaw @ git+${REPO}@main"
BIN="amplifier-openclaw"

info()  { echo "  ✓ $*"; }
step()  { echo ""; echo "→ $*"; }
fail()  { echo "  ✗ $*" >&2; exit 1; }

# --- Check Python ---
step "Checking Python..."
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
        info "Python $PY_VERSION"
    else
        fail "Python 3.11+ required (found $PY_VERSION). Install from https://python.org"
    fi
else
    fail "Python 3 not found. Install from https://python.org"
fi

# --- Install uv if missing ---
step "Checking uv..."
if command -v uv &>/dev/null; then
    info "uv $(uv --version 2>/dev/null | head -1)"
else
    step "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if command -v uv &>/dev/null; then
        info "uv installed"
    else
        fail "uv installation failed. Install manually: https://docs.astral.sh/uv/"
    fi
fi

# --- Install amplifier-openclaw ---
step "Installing amplifier-openclaw..."
uv tool install --force "$PACKAGE" 2>&1 | tail -5

if command -v "$BIN" &>/dev/null; then
    info "$BIN $($BIN --version 2>&1)"
else
    # uv tools go to ~/.local/bin — might not be on PATH
    if [ -f "$HOME/.local/bin/$BIN" ]; then
        info "Installed to ~/.local/bin/$BIN"
        echo ""
        echo "  ⚠  Add ~/.local/bin to your PATH:"
        echo "     export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
    else
        fail "Installation failed"
    fi
fi

# --- Done ---
echo ""
echo "  🚀 amplifier-openclaw is ready!"
echo ""
echo "  Quick start:"
echo "    amplifier-openclaw run \"Hello from Amplifier\""
echo "    amplifier-openclaw run --model gemini/gemini-2.5-flash \"Hello from Gemini\""
echo "    amplifier-openclaw --help"
echo ""
echo "  Requires at least one LLM API key in your environment:"
echo "    ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, etc."
echo ""
