# Quill

A local-AI writing assistant for [Omarchy](https://omarchy.org). Select text in
any application, press **SUPER+I** (or **SUPER+SHIFT+right-click**), pick an
edit, and Quill replaces the selection in place.

Everything runs on your own machine through [Ollama](https://ollama.com). No
text ever leaves the computer, and it works with no network connection.

```
Fix Spelling & Grammar   Rewrite for Clarity   Make It Shorter
Professional Tone        Friendly Tone         Plain English
Expand                   Translate to English  Custom Instruction…
```

---

## Why it is a popup and not a context-menu item

The obvious ask is "add an AI entry to the right-click menu of every text box".
That is not achievable on Wayland, and it is worth being precise about why.

A context menu is drawn by the application that owns the text box, using its own
toolkit. Chromium, Electron, GTK, Qt and every terminal each build their own,
and Wayland deliberately offers no way for one client to inject widgets into
another's menus. There is no system-wide menu to extend — the "Cut / Copy /
Paste" menu you see in Slack is Electron's, and only Electron can add to it.

So Quill does what every tool in this space does: it binds a trigger, reads the
selection, and pastes the result back. The popup opens **at the mouse cursor**
and is styled like a context menu, which gets the same feel without pretending
to an integration that cannot exist.

## Requirements

Omarchy ships everything except Ollama:

| Component | Provided by |
|---|---|
| `hyprctl`, `wl-copy`, `wl-paste`, `notify-send` | Omarchy base |
| `python-gobject`, `gtk4`, `gtk4-layer-shell`, `libadwaita` | Omarchy base |
| `ollama-cuda` / `ollama-rocm` / `ollama` | installed by `install.sh` |

## Install

```bash
cd ~/Projects/quill
./install.sh
```

The installer will ask for your sudo password (to install Ollama), then:

1. installs the right Ollama package for your GPU — the same NVIDIA/AMD
   detection Omarchy's own *Install → AI → Ollama* menu entry uses,
2. enables and starts the `ollama` service,
3. pulls the model (several GB — this is the slow step),
4. links `quill` into `~/.local/bin`,
5. writes `~/.config/quill/config.toml`,
6. appends the keybindings to `~/.config/hypr/bindings.lua`, backing the file up
   first, and reloads Hyprland.

It is safe to re-run. Useful flags: `--no-model`, `--no-keybind`.

Check the result at any time:

```bash
quill doctor
```

## Keybindings

| Chord | Action |
|---|---|
| `SUPER + I` | Open the Quill menu at the cursor |
| `SUPER + SHIFT + right-click` | Same, from the mouse |

`SUPER + SHIFT + right-click` rather than plain `SUPER + right-click` because
Omarchy binds the latter to drag-to-resize windows; this leaves that intact.

In the menu: `1`–`9` run an edit directly, `↑`/`↓` and `⏎` also work, `Esc`
closes. Pressing the trigger again while the menu is open dismisses it, the same
way Omasnap behaves.

On the result panel the text is **editable** — fix a near-miss in place rather
than re-running. `⏎` or **Replace** pastes over your selection, **Copy** puts it
on the clipboard, **Retry** re-runs the same edit.

## Models

Anything in `ollama list` works — set `model` in the config. Sensible choices for
a 16 GB GPU, all pullable with `ollama pull`:

| Model | VRAM | Notes |
|---|---|---|
| `gemma4:12b-it-qat` | ~8 GB | **Default.** Quantization-aware 4-bit, so quality holds up. Strongest multilingual option at this size. |
| `granite4.1:8b` | ~5 GB | Noticeably faster. Fine for spellcheck, weaker on nuanced rewriting. |
| `gemma4:26b-a4b-it-q4` | ~15 GB | Best quality, but nearly fills a 16 GB card and contends with the desktop. |
| `qwen3.6:35b-a3b` | ~20 GB | Spills into system RAM; strong, slower first token. |

`keep_alive` (default `30m`) is what makes the *second* invocation feel instant —
the model stays resident in VRAM. Set it to `"0"` to free the GPU immediately
after each edit.

## Configuration

`~/.config/quill/config.toml`, all keys optional. See
[`share/config.example.toml`](share/config.example.toml) for the annotated
version. Adding any `[[actions]]` block replaces the whole default menu, so list
every action you want; referencing a built-in `id` without an `instruction`
reuses its built-in prompt.

```toml
model = "gemma4:12b-it-qat"
keep_alive = "30m"
auto_replace = false   # true = paste as soon as the model finishes, no review

[[actions]]
id = "translate_ru"
label = "Translate to Russian"
instruction = "Translate the text into natural, idiomatic Russian."
```

## Other ways to use it

Bind a single edit to its own key, skipping the menu entirely:

```lua
o.bind("SUPER + SHIFT + I", "Quill: spellcheck", "/home/you/.local/bin/quill run fix")
```

Use it as a plain text filter:

```bash
echo "some txet" | quill filter fix
quill filter custom -i "Turn this into bullet points" < notes.txt
```

### Cleaning up dictation

Omarchy's dictation tool, `voxtype`, can pipe each transcription through an
external command. Point it at Quill and your dictation gets the same local model
applied to it — filler words removed, grammar fixed — before the text lands:

```toml
# ~/.config/voxtype/config.toml
[output.post_process]
command = "/home/you/.local/bin/quill filter fix"
timeout_ms = 30000
```

`quill filter` prints its input back out unchanged if Ollama is down or the
request fails, so a broken model can never eat a transcription.

Note that this shares Quill's model, not voxtype's. Voxtype's own model is
Whisper (`ggml-base.en.bin`), which is speech-to-text — it turns audio into
words and cannot rewrite text. The two jobs need two different kinds of model.

## Troubleshooting

**"Ollama is not running"** — `sudo systemctl start ollama`, then `quill doctor`.

**"model … is not installed"** — `ollama pull gemma4:12b-it-qat`.

**"Nothing selected"** — Quill reads the PRIMARY selection first and falls back
to sending a copy chord. A few apps do neither; copy manually first.

**The popup opens as a normal window** — `gtk4-layer-shell` must be preloaded
ahead of `libwayland-client`. `bin/quill` sets `LD_PRELOAD` for this; run Quill
through that script rather than calling `python -m quill` directly.

**First edit is slow** — that is the model loading into VRAM. Subsequent edits
within `keep_alive` are fast.

## Development

```bash
python3 tests/mock_ollama.py &        # canned streaming responses on :11434
./bin/quill menu                      # against the mock, no model needed
python3 tests/paste_harness.py "txet" # a text field to test replacement into
```

## Layout

```
bin/quill                 launcher (pins system python, sets LD_PRELOAD)
src/quill/cli.py          subcommands: menu, run, filter, doctor
src/quill/ui.py           GTK4 layer-shell popup
src/quill/hypr.py         hyprctl: cursor, monitors, key injection, focus
src/quill/clipboard.py    selection capture and in-place replacement
src/quill/ollama.py       streaming client + output sanitiser
src/quill/actions.py      the edit catalogue and prompts
src/quill/config.py       config.toml loading
install.sh                one-shot installer
```
