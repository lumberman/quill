"""Offline stand-ins shared by the popup and replacement regression tests.

Fakes stand in for GTK, the provider and worker threads, so the real Quill
source runs unchanged without a desktop. QUILL_SOURCE_ROOT may name another
src directory or checkout root, which is how a test is pointed at an older
revision to confirm it fails there.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True


def _source_root() -> Path:
    raw = os.environ.get("QUILL_SOURCE_ROOT")
    base = Path(raw).expanduser().resolve() if raw else Path(__file__).resolve().parents[1] / "src"
    for candidate in (base, base / "src"):
        if (candidate / "quill" / "__init__.py").is_file():
            return candidate
    raise SystemExit(f"QUILL_SOURCE_ROOT has no quill/__init__.py: {base}")



SRC = _source_root()


def _noop(*_args, **_kwargs):
    return None


class Anything:
    """Absorbs any attribute access or call. Used for enums and layer-shell calls."""

    def __init__(self, *_args, **_kwargs):
        pass

    def __call__(self, *_args, **_kwargs):
        return Anything()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Anything()

    def __iter__(self):
        return iter(())


class StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Anything()


class Buffer:
    def __init__(self):
        self.text = ""

    def set_text(self, text, *_args):
        self.text = text

    def get_text(self, _start=None, _end=None, *_args):
        return self.text

    def get_bounds(self):
        return 0, len(self.text)

    def get_start_iter(self):
        return 0

    def get_end_iter(self):
        return len(self.text)


class Widget:
    """Stateful where the tests look, permissive everywhere else."""

    def __init__(self, *_args, **kwargs):
        self._label = kwargs.get("label", "") or ""
        self._text = ""
        self._visible = True
        self._sensitive = True
        self._spinning = False
        self._focused = False
        self._children = []
        self._child_name = None
        self._buffer = Buffer()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _noop

    def set_label(self, label):
        self._label = label

    def get_label(self):
        return self._label

    def set_text(self, text):
        self._text = text

    def get_text(self):
        return self._text

    def set_visible(self, visible):
        self._visible = bool(visible)

    def get_visible(self):
        return self._visible

    is_visible = get_visible

    def set_sensitive(self, sensitive):
        self._sensitive = bool(sensitive)

    def get_sensitive(self):
        return self._sensitive

    is_sensitive = get_sensitive

    def start(self):
        self._spinning = True

    def stop(self):
        self._spinning = False

    def get_spinning(self):
        return self._spinning

    def grab_focus(self):
        self._focused = True
        return True

    def has_focus(self):
        return self._focused

    def append(self, child):
        self._children.append(child)

    def get_row_at_index(self, index):
        return self._children[index] if 0 <= index < len(self._children) else None

    def add_named(self, _child, _name):
        return None

    def set_visible_child_name(self, name):
        self._child_name = name

    def get_visible_child_name(self):
        return self._child_name

    def get_buffer(self):
        return self._buffer

    def connect(self, *_args, **_kwargs):
        return 1


class FakeWindowBase:
    def __init__(self, application=None, **_kwargs):
        self._app = application
        self._visible = False
        self.presented = 0

    def set_content(self, *_args):
        pass

    def add_css_class(self, *_args):
        pass

    def remove_css_class(self, *_args):
        pass

    def set_decorated(self, *_args):
        pass

    def set_resizable(self, *_args):
        pass

    def set_default_size(self, *_args):
        pass

    def add_controller(self, *_args):
        pass

    def set_focus(self, *_args):
        pass

    def grab_focus(self):
        return True

    def set_visible(self, visible):
        self._visible = bool(visible)

    def get_visible(self):
        return self._visible

    is_visible = get_visible

    def present(self):
        self._visible = True
        self.presented += 1

    def close(self):
        self._visible = False

    destroy = close

    def get_application(self):
        return self._app


class FakeGLib:
    """A deterministic main loop: callbacks run only when a test flushes."""

    PRIORITY_HIGH = -100
    PRIORITY_DEFAULT = 0
    PRIORITY_HIGH_IDLE = 100
    PRIORITY_DEFAULT_IDLE = 200
    PRIORITY_LOW = 300
    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True

    def __init__(self):
        self.reset()

    def reset(self):
        self.queue = []
        self._next_id = 0

    def _add(self, fn, args):
        self._next_id += 1
        self.queue.append((self._next_id, fn, args))
        return self._next_id

    def idle_add(self, fn, *args, **_kwargs):
        return self._add(fn, args)

    def timeout_add(self, _ms, fn, *args, **_kwargs):
        return self._add(fn, args)

    def timeout_add_seconds(self, _s, fn, *args, **_kwargs):
        return self._add(fn, args)

    def source_remove(self, source_id):
        before = len(self.queue)
        self.queue = [item for item in self.queue if item[0] != source_id]
        return len(self.queue) < before

    def flush(self, limit: int = 500) -> None:
        ran = 0
        while self.queue:
            ran += 1
            if ran > limit:
                raise AssertionError("idle queue never drained")
            source_id, fn, args = self.queue.pop(0)
            if fn(*args) is True:
                self.queue.append((source_id, fn, args))



GLIB = FakeGLib()
THREADS: list = []


class FakeThread:
    """Records workers; a test runs each one synchronously when it chooses."""

    def __init__(self, group=None, target=None, name=None, args=(),
kwargs=None, daemon=None):
        self.target = target
        self.args = tuple(args)
        self.kwargs = dict(kwargs or {})
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True
        THREADS.append(self)

    def run_now(self):
        self.target(*self.args, **self.kwargs)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None



FAKE_THREADING = types.SimpleNamespace(
    Thread=FakeThread, Event=threading.Event, Lock=threading.Lock,
    RLock=threading.RLock, current_thread=threading.current_thread,
    main_thread=threading.main_thread, get_ident=threading.get_ident)


class ProviderError(Exception):
    pass


def _make_stubs() -> dict:
    stubs = {name: StubModule(f"quill.{name}") for name in (
        "provider", "state", "style", "branding", "claudecode", "codex",
        "ollama", "openai_api", "openrouter", "credentials")}

    provider = stubs["provider"]
    provider.ProviderError = ProviderError
    provider.streams = []

    def stream_chat(cfg, messages, temperature, cancel=None, **_kwargs):
        if not provider.streams:
            raise AssertionError("unexpected provider request")
        item = provider.streams.pop(0)
        if isinstance(item, BaseException):
            raise item
        for delta in item:
            yield delta

    provider.stream_chat = stream_chat
    provider.label = lambda cfg: "Test model"
    provider.ready = lambda cfg: (True, "ready")

    state = stubs["state"]
    state.IDLE = "idle"
    state.WORKING = "working"
    state.write = _noop

    @contextlib.contextmanager
    def working(*_args, **_kwargs):
        yield

    state.working = working
    stubs["style"].apply = _noop
    stubs["branding"].NAME = "Quill"
    stubs["branding"].icon_path = lambda: ""
    stubs["ollama"].is_loaded = lambda cfg: True
    return stubs


def _install_gi() -> None:
    gi = types.ModuleType("gi")
    gi.require_version = _noop
    repo = types.ModuleType("gi.repository")

    gtk = StubModule("Gtk")
    for name in ("Box", "Label", "Stack", "ListBox", "ListBoxRow", "Entry",
                 "ScrolledWindow", "TextView", "Spinner", "Button",
                 "EventControllerKey", "Widget"):
        setattr(gtk, name, Widget)
    adw = StubModule("Adw")
    adw.ApplicationWindow = FakeWindowBase
    adw.Application = Anything
    gdk = StubModule("Gdk")
    gdk.keyval_name = lambda keyval: keyval if isinstance(keyval, str) else None

    repo.Gtk = gtk
    repo.Adw = adw
    repo.Gdk = gdk
    repo.Gio = StubModule("Gio")
    repo.GLib = GLIB
    repo.Pango = StubModule("Pango")
    repo.Gtk4LayerShell = StubModule("Gtk4LayerShell")
    gi.repository = repo
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo


def _load():
    for name in list(sys.modules):
        if name == "quill" or name.startswith("quill."):
            del sys.modules[name]
    _install_gi()
    spec = importlib.util.spec_from_file_location(
        "quill", SRC / "quill" / "__init__.py",
        submodule_search_locations=[str(SRC / "quill")])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["quill"] = pkg
    spec.loader.exec_module(pkg)
    stubs = _make_stubs()
    for short, module in stubs.items():
        sys.modules[f"quill.{short}"] = module
        setattr(pkg, short, module)
    real = {name: importlib.import_module(f"quill.{name}")
            for name in ("config", "sanitize", "hypr", "clipboard", "ui", "cli")}
    return stubs, real



STUBS, REAL = _load()
provider = STUBS["provider"]
config = REAL["config"]
hypr = REAL["hypr"]
clipboard = REAL["clipboard"]
ui = REAL["ui"]
cli = REAL["cli"]
print(f"quill source: {SRC}", file=sys.stderr)


# -- popup harness -----------------------------------------------------------

class FakeClipboardApi:
    def __init__(self):
        self.replaced: list[str] = []
        self.set_calls: list = []
        self.replace_result = True
        self.capture = ("teh text", "saved")

    def capture_selection(self, win=None, *_args, **_kwargs):
        return self.capture

    def replace_selection(self, text, win=None, saved_clipboard=None,
                          restore_clipboard=True, **_kwargs):
        self.replaced.append(text)
        return self.replace_result

    def set_clipboard(self, text):
        self.set_calls.append(text)
        return getattr(self, "copy_result", True)

    def clear_clipboard(self):
        self.set_calls.append(None)

    def restore_capture_if_owned(self, saved):
        return None


class FakeHypr:
    def __init__(self):
        self.notes: list[dict] = []
        self.closed: list = []

    def active_window(self):
        return {"address": "0xaaa", "tags": []}

    def cursor_pos(self):
        return 0, 0

    def monitor_at(self, _x, _y):
        return None

    def monitors(self):
        return []

    def is_terminal(self, _win):
        return False

    def notify(self, summary, body="", urgency="normal", replace_id=None,
               transient=False, timeout_ms=None, want_id=False):
        self.notes.append({"summary": summary, "body": body, "urgency": urgency})
        return 7 if want_id else None

    def close_notification(self, notification_id):
        self.closed.append(notification_id)


class FakeApp:
    def __init__(self):
        self.quits = 0

    def quit(self):
        self.quits += 1


class UiCase(unittest.TestCase):
    def setUp(self):
        GLIB.reset()
        THREADS.clear()
        provider.streams[:] = []
        self.clip = FakeClipboardApi()
        self.hyp = FakeHypr()
        for attr, value in (("clipboard", self.clip), ("hypr", self.hyp),
                            ("threading", FAKE_THREADING)):
            patcher = mock.patch.object(ui, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cfg = config.Config()
        self.cfg.auto_replace = False
        self.app = FakeApp()
        self.win = ui.QuillWindow(self.app, self.cfg, "teh text",
                                  {"address": "0xaaa", "tags": []}, "saved")
        self.win.present()

    def press(self, key):
        return self.win._on_key(None, key, 0, 0)

    def feed(self, thread, item):
        """Run one worker to completion; its callbacks wait in the idle queue."""
        self.assertTrue(thread.started)
        provider.streams.append(item)
        thread.run_now()

    def text(self):
        return self.win._result_text()

