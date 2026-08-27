# Quill

**Fix or rewrite text anywhere on your desktop with one keystroke — on-device,
so your words never leave the machine.**

Select text in any application — browser, editor, terminal, chat — press
**Super+I**, pick an edit, and Quill replaces the selection in place. There is
no window to switch to, nothing to paste into, and no round trip through a
chat box.

```
Fix Spelling & Grammar   Rewrite for Clarity   Make It Shorter
Professional Tone        Friendly Tone         Plain English
Expand                   Translate to English  Custom Instruction…
```

Or skip the menu entirely: **Super+Shift+I** fixes the grammar of whatever is
selected and puts it back, with no popup at all. Any edit in the list can take
a shortcut of its own.

### It runs on your own machine

By default every edit goes through [Ollama](https://ollama.com) on your own
GPU. No account, no API key, no network — it works on a plane. Quill ships no
model; you pick one in settings, which explains the trade-off in plain language
and downloads it for you.

Prefer a subscription you already pay for? Five backends are available, and the
settings window always states, in words, whether the selected text is about to
leave the computer:

| Backend | Cost | Text leaves the machine |
|---|---|---|
| **Ollama** (default) | free | no |
| **OpenAI-compatible server** | free locally | only if the URL is remote |
| **ChatGPT subscription** via Codex CLI | your existing plan | yes |
| **Claude subscription** via Claude Code | your existing plan | yes |
| **OpenRouter** | free tier or paid | yes |

Built for [Omarchy](https://omarchy.org) and Hyprland: a cursor-positioned
popup, a bar icon that spins while it thinks, and a libadwaita settings window
with a playground you can practise in before touching your own text.

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
3. does *not* download a model — you pick one in settings, which explains the trade-offs,
4. links `quill` into `~/.local/bin`,
5. writes `~/.config/quill/config.toml`,
6. appends the keybindings to `~/.config/hypr/bindings.lua`, backing the file up
   first, and reloads Hyprland,
7. installs the bar icon as an Omarchy shell plugin and adds it to your bar,
8. adds a window rule so the settings window floats instead of tiling.

It is safe to re-run. Useful flags: `--with-model` (download the default model
during install), `--no-keybind`, `--no-bar-icon`.

Check the result at any time:

```bash
quill doctor
```

## Keybindings

| Command | Action |
|---|---|
| `quill menu` | The popup (what the keybinding runs) |
| `quill run <id>` | Apply one edit directly |
| `quill filter <id>` | stdin → stdout |
| `quill settings` | Settings window |
| `quill login` / `logout` | OpenRouter sign-in |
| `quill models` | List available models |
| `quill doctor` | Check the install |

| Chord | Action |
|---|---|
| `SUPER + I` | Open the Quill menu at the cursor |
| `SUPER + SHIFT + right-click` | Same, from the mouse |
| `SUPER + SHIFT + I` | **Invisible mode** — fix grammar in place, no popup |

`SUPER + SHIFT + right-click` rather than plain `SUPER + right-click` because
Omarchy binds the latter to drag-to-resize windows; this leaves that intact.

The settings window opens with a **Shortcuts** list covering all of this. The
Hyprland chords in it are read from `hyprctl binds` rather than hardcoded, so it
stays truthful if you rebind them.

In the menu: `1`–`9` run an edit directly, `↑`/`↓` and `⏎` also work, `Esc`
closes. Pressing the trigger again while the menu is open dismisses it, the same
way Omasnap behaves.

On the result panel the text is **editable** — fix a near-miss in place rather
than re-running. `⏎` or **Replace** pastes over your selection, **Copy** puts it
on the clipboard, **Retry** re-runs the same edit.

## Invisible mode

`SUPER + SHIFT + I` runs one edit straight over the selection. No popup, no
result panel, no confirmation — the text just changes.

Feedback while it works is deliberately peripheral:

- a notification naming the edit and the backend, which closes itself the moment
  the text is replaced,
- the bar icon becomes a spinning circle and returns to the pen nib when done.

If the model decides nothing needed changing, you get a brief *"no changes
needed"* instead of silence — otherwise a no-op is indistinguishable from a
failure. Errors replace the same notification rather than stacking a second one.

Bind a different one-shot edit by changing the action id:

```lua
o.bind("SUPER + SHIFT + I", "Quill: Fix grammar in place",
       "/home/you/.local/bin/quill run fix")
o.bind("SUPER + ALT + I", "Quill: Make it shorter",
       "/home/you/.local/bin/quill run shorter")
```

Add `--quiet` to drop the notification, e.g. for scripts.

## Bar icon

Quill installs a small Omarchy shell plugin (`shell-plugin/`) that puts a
fountain-pen nib in the bar, next to the dictation microphone:

- **Click it** for settings.
- **Right-click it** to run an edit on the current selection.

Editing is on right-click because `SUPER+I` already covers it from the keyboard,
which leaves the plain click free for the thing that has no other shortcut.
- **While a model request is running** the nib becomes a wand, so a slow edit is
  visible from the bar.

It is a third-party plugin under `~/.config/omarchy/plugins/quill.writer`, not a
patch to `/usr/share/omarchy`, so Omarchy updates will not overwrite it. The
widget reads `$XDG_RUNTIME_DIR/quill/state.json` through a Quickshell `FileView`;
Quill writes that file with an atomic rename, so there is no follower process and
no torn read.

Remove it by deleting the symlink and the `quill.writer` entry from
`~/.config/omarchy/shell.json`, then running `omarchy-restart-shell`.

## Using your ChatGPT subscription

If you pay for ChatGPT, you can run edits on that plan instead of buying API
tokens separately. Quill drives [OpenAI's own Codex
CLI](https://developers.openai.com/codex/cli), which supports signing in with a
ChatGPT account:

```bash
codex login            # once — "Logged in using ChatGPT"
```

Then pick **ChatGPT subscription (Codex CLI)** in settings, or set
`provider = "codex"`. Quill checks `auth_mode` in `~/.codex/auth.json` and tells
you in settings whether you are on a subscription or an API key, because the
second one *is* billed per token.

This is the sanctioned route: OpenAI's own client, OpenAI's own OAuth, under
their terms. Quill does not touch ChatGPT's private web endpoints and does not
reuse browser session cookies — that would breach OpenAI's terms of service, and
no amount of convenience is worth building on something that can be shut off or
get an account banned.

Trade-offs worth knowing:

- **Slower.** An agent process starts per edit, so expect a few seconds against
  a fraction of one locally.
- **No streaming.** The CLI hands back a final message, so the result appears all
  at once. Quill shows a spinner instead of pretending otherwise.
- **Quota, not tokens.** Edits count against your plan's rate limits.

Quill runs Codex with `--sandbox read-only`, `--ephemeral`,
`--ignore-user-config` and `--ignore-rules`, rooted at an empty temp directory,
so an unrelated `AGENTS.md` cannot change how your text is edited and the agent
has nothing of yours to read.

## Using a local OpenAI-compatible server

Anything exposing `/v1/chat/completions` works — LM Studio, llama.cpp's server,
vLLM, LocalAI — as does `api.openai.com` itself. Pick a preset in settings or set
the base URL by hand:

```toml
provider = "openai"
openai_base_url = "http://127.0.0.1:1234/v1"   # LM Studio
openai_model = "your-model-id"
```

An API key is only required when the URL is not loopback; the settings window
says which. Keys are stored the same way as the OpenRouter one, never in
`config.toml`.

If you point this at Ollama, prefer the **Ollama** provider instead. This API has
no agreed way to switch off reasoning, and there is no field every server
accepts — strict ones reject unknown keys with a 400. On the same machine and
model, a one-line edit measured 8.3s through `/v1` against 0.28s natively.

## Using OpenRouter instead

Quill can run edits through OpenRouter rather than locally. Useful when you want
a bigger model than the GPU can hold, or on a machine with no GPU at all.

**Understand the trade first.** With OpenRouter selected, the text you highlight
is sent to OpenRouter and on to whichever provider serves that model. Free models
in particular are commonly trained on. Do not point it at anything confidential;
keep the local provider for that. Quill shows this in the settings window rather
than burying it here.

Connect by signing in with your OpenRouter account:

```bash
quill login
```

That opens your browser, you approve, and Quill receives a key scoped to your own
account. It is OAuth 2.0 with PKCE, so no client secret is involved and the
authorization code is useless to anyone who intercepts it. Or paste an existing
key into the settings window instead — same result.

```bash
quill models          # free text models available right now
quill models --all    # include paid ones
quill logout          # forget the key locally
```

`quill logout` only removes the local copy. Revoke the key itself at
[openrouter.ai/settings/keys](https://openrouter.ai/settings/keys).

### Where the key is stored

In your login keyring via `secret-tool` (libsecret) when one is running,
otherwise in `~/.config/quill/credentials.json` with `0600` permissions. Never in
`config.toml`, which people commit to dotfiles repos. `$OPENROUTER_API_KEY`
overrides both if set.

### Free models

The model list is fetched live and filtered to models that are free in both
directions and actually emit text — the free tier also contains image and music
models that would fail confusingly on a rewrite. Free models are rate-limited;
if you hit a 429, Quill tells you to wait or switch back to local.

Two environment variables exist for testing and self-hosted proxies:
`QUILL_OPENROUTER_API_BASE` and `QUILL_OPENROUTER_AUTH_URL`.

## Models

Quill ships no models. Settings lists the recommended ones whether or not they
are on disk, each with a **Download** button and a progress bar, so the first
run has somewhere to go. Installed models appear as radio rows — always visible, each
one saying in plain language what picking it costs you. Models Quill measured
and rejected are folded into an "Other models you have installed" group so they
are still reachable without sitting beside the answers. The numbers come from
Quill's own benchmark
(`tests/bench_tasks.py`, 14 tasks covering every edit plus Chinese and Russian
translation, markdown preservation and fact retention):

| Model | Size | Per edit | Score | |
|---|---|---|---|---|
| `gemma4:12b-it-qat` | 7.2 GB | ~315 ms | **13/14** | Best quality. Gets proper nouns, agreement and non-English orthography right. |
| `granite4.1:3b` | 2.1 GB | ~113 ms | 10/14 | Nearly 3× faster. Sometimes leaves a sentence uncapitalised and is weaker on Russian and Chinese. |

Both feel instant. The larger one makes fewer mistakes, and matters most if you
edit Chinese or Russian.

Models below ~3B were measured and rejected: `qwen3:0.6b`, `llama3.2:1b`,
`gemma3:270m` and `smollm2:135m` all scored 1/4 on grammar — they fix spelling
and leave the grammar alone. The dropdown marks them so choosing one is not a
neutral-looking decision.

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

### Thinking models

Quill sends `think: false`. This matters more than it sounds: asked to fix one
line of spelling, `gemma4:12b-it-qat` with reasoning enabled spent **95 seconds
and 7962 tokens** deliberating, hit the context limit, and returned an empty
answer. The identical request with `think: false` took **0.2 seconds and 8
tokens**. Editing text is a transformation, not a puzzle.

Models with no reasoning channel reject the field; Quill notices, drops it, and
retries. Set `think = true` to opt back in, or omit the key to leave the model's
default alone.

## Settings

```bash
quill settings
```

…or click the bar icon. The window opens with the shortcut set in keycaps and a
three-step tutorial that uses the *real* shortcut on real text: select the
sample, press the chord, and Quill replaces it in place and shows you what
changed. The steps are driven by what you actually do — selecting the sample
arms step two, and the buffer changing under the paste completes it — so it is a
rehearsal rather than a simulation, and it doubles as a check that your chosen
model works.

Below that, everything in the config file:

- **Model** — picked from a list of what you actually have pulled, with a live
  "Ollama is running · <model> is installed" check and a refresh button
- **Keep in memory**, context window, Ollama host
- **Let the model think first** — off by default, and the row explains why
- **Replace without reviewing** (on by default), restore-clipboard, timeout
- **Menu** — rename edits, rewrite their prompts, change temperature, reorder,
  add and remove

Changes apply on **Save**; closing the window discards them. Saving rewrites
`config.toml`, which means hand-written comments in that file are lost — the
settings themselves survive. If you would rather keep a commented file, edit it
by hand and leave this window alone.

## Configuration

`~/.config/quill/config.toml`, all keys optional. See
[`share/config.example.toml`](share/config.example.toml) for the annotated
version. Adding any `[[actions]]` block replaces the whole default menu, so list
every action you want; referencing a built-in `id` without an `instruction`
reuses its built-in prompt.

```toml
model = "gemma4:12b-it-qat"
keep_alive = "30m"
auto_replace = true    # false = show the result and confirm before replacing

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

**An edit takes tens of seconds and returns nothing** — the model is reasoning
instead of answering. Check `think` is not set to `true` in your config.

## Development

```bash
python3 tests/mock_ollama.py &        # canned streaming responses on :11434
python3 tests/mock_openrouter.py &    # OpenRouter stand-in that verifies PKCE
./bin/quill menu                      # against the mock, no model needed
python3 tests/paste_harness.py "txet" # a text field to test replacement into
```

## Layout

```
bin/quill                 launcher (pins system python, sets LD_PRELOAD)
shell-plugin/             Omarchy bar icon (Quickshell plugin)
src/quill/cli.py          subcommands: menu, run, filter, doctor
src/quill/ui.py           GTK4 layer-shell popup
src/quill/hypr.py         hyprctl: cursor, monitors, key injection, focus
src/quill/clipboard.py    selection capture and in-place replacement
src/quill/ollama.py       Ollama streaming client
src/quill/openrouter.py   OpenRouter client + OAuth PKCE sign-in
src/quill/openai_api.py   any OpenAI-compatible server
src/quill/openai_compat.py shared /chat/completions streaming
src/quill/codex.py        ChatGPT subscription via the Codex CLI
src/quill/provider.py     backend dispatch
src/quill/credentials.py  API key storage (keyring, else 0600 file)
src/quill/sanitize.py     output sanitiser (provider-neutral)
src/quill/actions.py      the edit catalogue and prompts
src/quill/config.py       config.toml loading
src/quill/state.py        idle/working state for the bar icon
src/quill/settings.py     libadwaita settings window
install.sh                one-shot installer
```
