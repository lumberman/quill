#!/usr/bin/env bash
# Quill installer for Omarchy.
#
#   ./install.sh                 full install
#   ./install.sh --with-model    also download the default model now
#   ./install.sh --no-keybind    do not touch ~/.config/hypr/bindings.lua
#   ./install.sh --no-bar-icon   do not add the icon to the Omarchy bar
set -euo pipefail

QUILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/quill"
BINDINGS="$HOME/.config/hypr/bindings.lua"
HYPRLAND_LUA="$HOME/.config/hypr/hyprland.lua"
MARK_START="-- >>> quill >>>"
MARK_END="-- <<< quill <<<"

DO_MODEL=0
DO_KEYBIND=1
DO_BAR=1
for arg in "$@"; do
  case "$arg" in
    --with-model) DO_MODEL=1 ;;
    --no-keybind) DO_KEYBIND=0 ;;
    --no-bar-icon) DO_BAR=0 ;;
    -h|--help)    sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;35m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# Elevate via SUDO_ASKPASS when one is set. Without a controlling terminal --
# launched from a keybinding, or driven by a tool -- plain sudo has nowhere to
# prompt, and a GUI askpass is the only way to ask for the password.
run_root() {
  if [[ $EUID -eq 0 ]]; then
    "$@"
  elif [[ -n "${SUDO_ASKPASS:-}" ]]; then
    sudo -A "$@"
  else
    sudo "$@"
  fi
}

# Read the model out of the user's config if they already have one, so a
# re-run pulls what they actually use rather than the default.
MODEL="$(sed -n 's/^[[:space:]]*model[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
          "$CONFIG_DIR/config.toml" 2>/dev/null | head -1)"
MODEL="${MODEL:-gemma4:12b-it-qat}"

# Append a marked block to a Hyprland config file, once. The marker starts with
# "--", so every grep over it needs `--` to end option parsing.
append_hypr_block() {
  local file="$1" what="$2" body="$3"
  if [[ ! -f "$file" ]]; then
    warn "$file not found — skipping $what"
    return
  fi
  if grep -qF -- "$MARK_START" "$file"; then
    say "$what already present in $(basename "$file")"
    return
  fi
  local backup="$file.bak.$(date +%Y%m%d-%H%M%S)-before-quill"
  cp "$file" "$backup"
  say "Adding $what (backup: $(basename "$backup"))"
  printf '\n%s\n%s\n%s\n' "$MARK_START" "$body" "$MARK_END" >> "$file"
}

# --- 1. dependencies -------------------------------------------------------
say "Checking dependencies"
missing=()
for tool in hyprctl wl-copy wl-paste notify-send; do
  command -v "$tool" >/dev/null || missing+=("$tool")
done
if ((${#missing[@]})); then
  echo "Missing required tools: ${missing[*]}" >&2
  echo "Install with: sudo pacman -S --needed hyprland wl-clipboard libnotify" >&2
  exit 1
fi

if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Gtk4LayerShell","1.0")' 2>/dev/null; then
  say "Installing GTK dependencies"
  run_root pacman -S --needed --noconfirm python-gobject gtk4 gtk4-layer-shell libadwaita
fi

# --- 2. ollama -------------------------------------------------------------
if ! command -v ollama >/dev/null; then
  # Same GPU detection Omarchy's own "Install > AI > Ollama" menu entry uses.
  if command -v nvidia-smi >/dev/null; then
    OLLAMA_PKG=ollama-cuda
  elif command -v rocminfo >/dev/null; then
    OLLAMA_PKG=ollama-rocm
  else
    OLLAMA_PKG=ollama
  fi
  say "Installing $OLLAMA_PKG"
  run_root pacman -S --needed --noconfirm "$OLLAMA_PKG"
else
  say "Ollama already installed"
fi

say "Starting the Ollama service"
if systemctl list-unit-files ollama.service >/dev/null 2>&1; then
  run_root systemctl enable --now ollama.service || warn "Could not enable ollama.service"
else
  systemctl --user enable --now ollama.service || warn "Could not enable user ollama.service"
fi

for _ in $(seq 1 30); do
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
  || warn "Ollama is not answering on :11434 yet — check 'systemctl status ollama'"

# --- 3. model --------------------------------------------------------------
if ((DO_MODEL)); then
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    say "Model $MODEL already present"
  else
    say "Pulling $MODEL (several GB — this is the long part)"
    ollama pull "$MODEL"
  fi
else
  # Quill ships no models. Which one to use is a real choice with real
  # trade-offs, so it belongs in settings where those are explained, not in an
  # installer that picks for you.
  say "No model downloaded — choose one in Quill's settings"
fi

# --- 4. link the command ---------------------------------------------------
say "Linking quill into $BIN_DIR"
mkdir -p "$BIN_DIR"
ln -sf "$QUILL_ROOT/bin/quill" "$BIN_DIR/quill"

# --- 5. config -------------------------------------------------------------
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.toml" ]]; then
  say "Writing $CONFIG_DIR/config.toml"
  cp "$QUILL_ROOT/share/config.example.toml" "$CONFIG_DIR/config.toml"
else
  say "Keeping your existing config.toml"
fi

# --- 6. keybindings and window rules ---------------------------------------
if ((DO_KEYBIND)); then
  append_hypr_block "$BINDINGS" "keybindings" "$(cat <<LUA
-- Quill: local-AI writing assistant. Select text in any app, then trigger to
-- spellcheck or rewrite it in place. SUPER+SHIFT+right-click is used rather
-- than SUPER+right-click so Omarchy's drag-to-resize binding stays intact.
o.bind("SUPER + I", "Quill: Open the edit menu", "$BIN_DIR/quill menu")
o.bind("SUPER + SHIFT + mouse:273", "Quill: Open the edit menu", "$BIN_DIR/quill menu")
-- Invisible mode: fix the selection in place, no popup. Change "fix" to any
-- other action id to bind a different one-shot edit.
o.bind("SUPER + SHIFT + I", "Quill: Fix grammar in place", "$BIN_DIR/quill run fix")
LUA
)"

  # Window rules are not keybindings, so they go where the other rules live.
  append_hypr_block "$HYPRLAND_LUA" "window rules" "$(cat <<'LUA'
-- Quill's settings window is a dialog, not something to tile into the
-- workspace.
o.window("com.omarchy.Quill.Settings", { float = true })
o.window("com.omarchy.Quill.Settings", { center = true })
o.window("com.omarchy.Quill.Settings", { size = { 720, 840 } })
LUA
)"

  hyprctl reload >/dev/null 2>&1 || warn "Run 'hyprctl reload' to pick up the new bindings"
fi


# --- 7. bar icon -----------------------------------------------------------
OMARCHY_CONFIG="$HOME/.config/omarchy"
SHELL_JSON="$OMARCHY_CONFIG/shell.json"

if ((DO_BAR)); then
  if [[ ! -d "$OMARCHY_CONFIG" ]]; then
    warn "No ~/.config/omarchy — skipping the bar icon"
  else
    mkdir -p "$OMARCHY_CONFIG/plugins"
    # Symlink rather than copy so the plugin tracks the repo, the same way
    # other third-party Omarchy plugins are installed.
    ln -sfn "$QUILL_ROOT/shell-plugin" "$OMARCHY_CONFIG/plugins/quill.writer"
    say "Linked the bar plugin"

    if [[ -f "$SHELL_JSON" ]]; then
      cp "$SHELL_JSON" "$SHELL_JSON.bak.$(date +%Y%m%d-%H%M%S)-before-quill"
      if /usr/bin/python3 - "$SHELL_JSON" <<'PYEOF'
import json, sys, pathlib

path = pathlib.Path(sys.argv[1])
cfg = json.loads(path.read_text())
layout = cfg.setdefault("bar", {}).setdefault("layout", {})

for section in layout.values():
    if isinstance(section, list) and any(
        isinstance(w, dict) and w.get("id") == "quill.writer" for w in section
    ):
        print("already in the bar layout")
        raise SystemExit(1)

center = layout.setdefault("center", [])
# Sit right after the indicator cluster so it reads as one group with the
# dictation icon rather than drifting off past the clock.
index = next(
    (i for i, w in enumerate(center)
     if isinstance(w, dict) and w.get("id") == "omarchy.indicators"),
    -1,
)
center.insert(index + 1 if index >= 0 else len(center),
              {"id": "quill.writer", "command": "quill menu", "alwaysShow": True})

# ensure_ascii=False matches how the shell serialises this file itself.
path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
print("added to the bar layout")
PYEOF
      then
        command -v omarchy-restart-shell >/dev/null && omarchy-restart-shell >/dev/null 2>&1 || true
      fi
    else
      warn "No shell.json — add the \"quill.writer\" widget from the bar settings"
    fi
  fi
fi

# --- 8. report -------------------------------------------------------------
echo
"$QUILL_ROOT/bin/quill" doctor || true
echo
say "Done. Select some text anywhere and press SUPER+I (or SUPER+SHIFT+right-click)."
