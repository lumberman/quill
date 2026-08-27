"""Claude-subscription backend, via Anthropic's own Claude Code CLI.

Same arrangement as codex.py: `claude` signs in with a Claude account and
`claude -p` runs non-interactively against it, so edits come out of the plan
rather than metered API tokens. Quill only drives that CLI.

Quill does not touch claude.ai's private endpoints or reuse browser cookies.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

# Aliases the CLI accepts, fastest first. Measured on a Max plan: haiku 3.6s,
# fable 5.0s for a one-line grammar fix.
MODELS = ("haiku", "fable", "sonnet", "opus")
DEFAULT_MODEL = "haiku"

MODEL_LABELS = {
    "haiku": "Haiku — fastest",
    "fable": "Fable — fast, stronger",
    "sonnet": "Sonnet — balanced",
    "opus": "Opus — most capable, slowest",
}

# Nothing here should touch the filesystem or network on Quill's behalf.
_BLOCKED_TOOLS = "Bash,Read,Write,Edit,NotebookEdit,WebFetch,WebSearch,Task,Glob,Grep"


class ClaudeCodeError(RuntimeError):
    pass


def binary() -> str | None:
    """Resolve the real executable.

    `claude` is often a mise shim that only works when mise is active, so a
    bare which() can hand back something that fails outside a login shell.
    """
    found = shutil.which("claude")
    if found and _runs(found):
        return found
    for candidate in sorted(
        glob.glob(str(Path.home() / ".local/share/mise/installs/claude/*/claude")),
        reverse=True,
    ):
        if _runs(candidate):
            return candidate
    return None


def _runs(path: str) -> bool:
    try:
        res = subprocess.run([path, "--version"], capture_output=True,
                             text=True, timeout=20, check=False)
        return res.returncode == 0 and "Claude Code" in res.stdout
    except (subprocess.SubprocessError, OSError):
        return False


def available() -> bool:
    return binary() is not None


def _credentials() -> dict:
    home = os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    try:
        return json.loads((Path(home) / ".credentials.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def subscription() -> str | None:
    """The plan name when signed in with a Claude account, else None."""
    oauth = _credentials().get("claudeAiOauth") or {}
    kind = oauth.get("subscriptionType")
    return str(kind) if kind else None


def signed_in() -> bool:
    return subscription() is not None


def describe() -> str:
    if not available():
        return "Claude Code is not installed"
    plan = subscription()
    if plan is None:
        return "Claude Code is installed but not signed in — run: claude"
    return f"Signed in on a Claude {plan} plan (uses your subscription, not API tokens)"


def stream_chat(cfg, messages: list[dict], temperature: float = 0.2,
                cancel: threading.Event | None = None) -> Iterator[str]:
    """Runs to completion and yields once; print mode has no token stream."""
    exe = binary()
    if not exe:
        raise ClaudeCodeError(
            "Claude Code is not installed. See https://claude.com/claude-code")
    if not signed_in():
        raise ClaudeCodeError("Claude Code is not signed in. Run: claude")

    from .codex import flatten_messages

    with tempfile.TemporaryDirectory(prefix="quill-claude-") as workdir:
        cmd = [
            exe, "-p",
            "--model", cfg.claude_model or DEFAULT_MODEL,
            # Skip MCP servers entirely: they add seconds and Quill needs none.
            "--strict-mcp-config",
            # One comma-joined value, not several arguments -- this flag is
            # variadic and will otherwise swallow whatever follows it.
            "--disallowed-tools", _BLOCKED_TOOLS,
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
                # An empty cwd so a stray CLAUDE.md cannot change how text is
                # edited, and so the agent has nothing of the user's to read.
                cwd=workdir,
            )
        except OSError as exc:
            raise ClaudeCodeError(f"Could not start Claude Code: {exc}") from exc

        # The prompt goes on stdin for the same reason as above.
        try:
            proc.stdin.write(flatten_messages(messages))
            proc.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            proc.kill()
            raise ClaudeCodeError(f"Claude Code closed early: {exc}") from exc

        deadline = time.monotonic() + cfg.request_timeout
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                return
            if time.monotonic() > deadline:
                proc.kill()
                raise ClaudeCodeError(
                    f"Claude Code did not answer within {cfg.request_timeout:.0f}s")
            time.sleep(0.1)

        out = proc.stdout.read() if proc.stdout else ""
        err = proc.stderr.read() if proc.stderr else ""
        if proc.returncode != 0:
            raise ClaudeCodeError(
                f"Claude Code exited {proc.returncode}: {err.strip()[:400]}")
        if not out.strip():
            raise ClaudeCodeError("Claude Code returned an empty reply")
        yield out
