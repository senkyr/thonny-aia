# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""Test fixtures: the real ``thonny`` package with a fake workbench, so the AIA logic
can be tested without a Tk window or any LLM SDK installed. Nothing here talks to a network."""

import sys
from typing import Dict, List, Optional

import pytest

class FakeWorkbench:
    def __init__(self, with_secrets: bool = True):
        self.options: Dict[str, object] = {}
        self.handlers: Dict[str, list] = {}
        self.queued: List[tuple] = []
        self.assistants: Dict[str, object] = {}
        self.editor = None
        if with_secrets:
            self.secrets: Dict[str, Optional[str]] = {}
            self.get_secret = lambda name, default=None: self.secrets.get(name, default)
            self.set_secret = lambda name, value: self.secrets.__setitem__(name, value)

    def set_default(self, name, value):
        self.options.setdefault(name, value)

    def get_option(self, name, default=None):
        return self.options.get(name, default)

    def set_option(self, name, value):
        self.options[name] = value

    def bind(self, sequence, func, add=None):
        self.handlers.setdefault(sequence, []).append(func)

    def unbind(self, sequence, func=None):
        handlers = self.handlers.get(sequence, [])
        if func in handlers:
            handlers.remove(func)

    def queue_event(self, sequence, event=None):
        # Real Thonny delivers this on the UI thread ~20 ms later; the tests
        # deliver it immediately on the calling thread.
        for handler in list(self.handlers.get(sequence, [])):
            handler(event)

    def get_editor_notebook(self):
        wb = self

        class _Notebook:
            def get_current_editor(self):
                return wb.editor

        return _Notebook()


@pytest.fixture
def fake_thonny(monkeypatch):
    """Point ``thonny.get_workbench`` at a fake workbench and return it."""
    import thonny

    wb = FakeWorkbench()
    monkeypatch.setattr(thonny, "get_workbench", lambda: wb)
    monkeypatch.setattr(thonny, "get_version", lambda: "5.5.0")

    # AIA modules cache `get_workbench` at import time - re-import them.
    for name in list(sys.modules):
        if name.startswith("thonny.plugins.aia"):
            monkeypatch.delitem(sys.modules, name)
    return wb


class ReadyLocalModels:
    """Stands in for local_models.LocalModels: the local model is ready, no thread, no network."""

    def __init__(self, phase="ready", host=None, **status):
        self.phase = phase
        self._host = host
        self.extra = status
        self.calls = []

    def status(self):
        return {"phase": self.phase, "seq": 1, **self.extra}

    def ready(self):
        return self.phase in ("ready", "system")

    def host(self):
        return self._host

    def __getattr__(self, name):  # configure, prepare, pause ... are recorded
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args: self.calls.append((name,) + args)


@pytest.fixture(autouse=True)
def local_models_ready(request, monkeypatch):
    """Tests of the keeper, the panel and the core see a prepared local model unless they set
    another one (cortex-zamestnani#120)."""
    if "fake_thonny" in request.fixturenames:
        request.getfixturevalue("fake_thonny")  # it drops the AIA modules: patch the fresh ones
    from thonny.plugins.aia import local_models

    manager = ReadyLocalModels()
    monkeypatch.setattr(local_models, "_manager", manager)
    return manager
