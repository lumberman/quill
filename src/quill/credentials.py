"""API key storage.

Keys go in the login keyring via libsecret when one is running. The fallback is
a 0600 file, which is weaker but still better than putting a credential in
config.toml where it would end up in a dotfiles repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

SERVICE = "quill"
ENV_VAR = "OPENROUTER_API_KEY"


def _fallback_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "quill" / "credentials.json"


def _secret_tool() -> str | None:
    return shutil.which("secret-tool")


def _keyring_store(account: str, secret: str) -> bool:
    tool = _secret_tool()
    if not tool:
        return False
    try:
        res = subprocess.run(
            [tool, "store", "--label", f"Quill ({account})",
             "service", SERVICE, "account", account],
            input=secret, text=True, capture_output=True, timeout=15, check=False,
        )
        return res.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _keyring_lookup(account: str) -> str | None:
    tool = _secret_tool()
    if not tool:
        return None
    try:
        res = subprocess.run(
            [tool, "lookup", "service", SERVICE, "account", account],
            text=True, capture_output=True, timeout=15, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def _keyring_clear(account: str) -> None:
    tool = _secret_tool()
    if not tool:
        return
    try:
        subprocess.run([tool, "clear", "service", SERVICE, "account", account],
                       capture_output=True, timeout=15, check=False)
    except (subprocess.SubprocessError, OSError):
        pass


def _file_read() -> dict:
    try:
        return json.loads(_fallback_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _file_write(data: dict) -> None:
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Narrow the permissions before the file is in place, not after, so the
    # secret is never briefly world-readable.
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def get(account: str) -> str | None:
    """Env var wins, so a shell or systemd unit can override without writing."""
    if account == "openrouter":
        from_env = os.environ.get(ENV_VAR, "").strip()
        if from_env:
            return from_env
    found = _keyring_lookup(account)
    if found:
        return found
    value = _file_read().get(account)
    return value.strip() if isinstance(value, str) and value.strip() else None


def set(account: str, secret: str) -> str:
    """Returns where it landed, so the UI can be honest about it."""
    secret = secret.strip()
    if not secret:
        raise ValueError("empty secret")
    if _keyring_store(account, secret):
        return "keyring"
    data = _file_read()
    data[account] = secret
    _file_write(data)
    return str(_fallback_path())


def clear(account: str) -> None:
    _keyring_clear(account)
    data = _file_read()
    if account in data:
        del data[account]
        _file_write(data)


def source(account: str) -> str | None:
    """Where the key currently comes from, without revealing it."""
    if account == "openrouter" and os.environ.get(ENV_VAR, "").strip():
        return f"${ENV_VAR}"
    if _keyring_lookup(account):
        return "keyring"
    if _file_read().get(account):
        return str(_fallback_path())
    return None


def redact(secret: str) -> str:
    if len(secret) <= 12:
        return "•" * len(secret)
    return f"{secret[:8]}…{secret[-4:]}"
