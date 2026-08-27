"""ChatGPT-subscription backend, via OpenAI's own Codex CLI.

This is the sanctioned way to run on a ChatGPT plan rather than metered API
tokens: `codex login` signs in with the user's ChatGPT account and `codex exec`
runs non-interactively against it. Quill just drives that CLI.

Quill does not, and will not, talk to ChatGPT's private web endpoints or reuse
browser session cookies. That would breach OpenAI's terms; this does not.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

BINARY = "codex"

# A ChatGPT account restricts which models Codex may use, so the speed knob is
# reasoning effort, not model choice. Measured: low 4.5s, high 5.5s, same answer.
EFFORTS = ("low", "medium", "high")
EFFORT_LABELS = {
    "low": "Fast — least deliberation",
    "medium": "Balanced",
    "high": "Thorough — slowest",
}


class CodexError(RuntimeError):
    pass


def binary() -> str | None:
    return shutil.which(BINARY)


def available() -> bool:
    return binary() is not None


def auth_mode() -> str | None:
    """'chatgpt' when signed in with a subscription, 'apikey' when billed."""
    home = os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    try:
        data = json.loads((Path(home) / "auth.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = data.get("auth_mode")
    return str(mode) if mode else None


def signed_in() -> bool:
    return auth_mode() is not None


def configured_model() -> str:
    """Whatever the user's own Codex config selects, for display only."""
    home = os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    try:
        text = (Path(home) / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    import re
    match = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else ""


def describe() -> str:
    if not available():
        return "Codex CLI is not installed"
    mode = auth_mode()
    if mode is None:
        return "Codex is installed but not signed in — run: codex login"
    if mode == "chatgpt":
        return "Signed in with ChatGPT (uses your subscription, not API tokens)"
    return f"Signed in with {mode} (this is billed per token)"


def flatten_messages(messages: list[dict]) -> str:
    """Codex takes one prompt string, so fold the system turn into it."""
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    user = "\n".join(m["content"] for m in messages if m.get("role") != "system")
    extra = (
        "You are being run as a non-interactive text filter. Do not use tools, "
        "do not read or write files, do not run commands, and do not ask "
        "questions. Reply with the edited text only."
    )
    return f"{system}\n{extra}\n\n{user}" if system else f"{extra}\n\n{user}"


def stream_chat(cfg, messages: list[dict], temperature: float = 0.2,
                cancel: threading.Event | None = None) -> Iterator[str]:
    """Runs to completion and yields once — the CLI has no token stream to tap.

    The UI shows a spinner throughout, which is honest: there is nothing
    partial to display.
    """
    exe = binary()
    if not exe:
        raise CodexError("Codex CLI is not installed. See https://developers.openai.com/codex/cli")
    if not signed_in():
        raise CodexError("Codex is not signed in. Run: codex login")

    with tempfile.TemporaryDirectory(prefix="quill-codex-") as workdir:
        out_file = Path(workdir) / "reply.txt"
        cmd = [
            exe, "exec",
            # Leave no session files behind; this is a text filter, not a chat.
            "--ephemeral",
            "--skip-git-repo-check",
            # Ignore the user's Codex config and rules so an unrelated AGENTS.md
            # or execpolicy cannot change how an edit behaves.
            "--ignore-user-config",
            "--ignore-rules",
            # Read-only sandbox rooted at an empty temp dir: nothing of the
            # user's is reachable even if the model tries.
            "--sandbox", "read-only",
            "--cd", workdir,
            "--color", "never",
            "--output-last-message", str(out_file),
        ]
        if cfg.codex_model:
            cmd += ["--model", cfg.codex_model]
        if cfg.codex_effort:
            cmd += ["-c", f'model_reasoning_effort="{cfg.codex_effort}"']
        cmd.append(flatten_messages(messages))

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=workdir,
            )
        except OSError as exc:
            raise CodexError(f"Could not start Codex: {exc}") from exc

        deadline = time.monotonic() + cfg.request_timeout
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                return
            if time.monotonic() > deadline:
                proc.kill()
                raise CodexError(
                    f"Codex did not answer within {cfg.request_timeout:.0f}s"
                )
            time.sleep(0.1)

        stderr = (proc.stderr.read() if proc.stderr else "") or ""
        if proc.returncode != 0:
            raise CodexError(f"Codex exited {proc.returncode}: {stderr.strip()[:400]}")

        try:
            reply = out_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise CodexError(f"Codex produced no reply: {exc}") from exc

        if not reply.strip():
            raise CodexError("Codex returned an empty reply")
        yield reply
