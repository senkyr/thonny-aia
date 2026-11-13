# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""Request handling of the _panel AIAView without a Tk window.

The view is built with ``__new__`` and its few Tk touch points (text insert,
busy state, ``after`` polling) are replaced by recorders, so the logic around
request ids, cancelling and the worker thread can run in plain pytest.
"""

import queue
import threading

import pytest


def make_view(module):
    view = module.AIAView.__new__(module.AIAView)
    view._conversation = []
    view._events = queue.Queue()
    view._request_id = 0
    view._request_provider_id = None
    view._cancel_event = None
    view._partial = []
    view._polling = False
    view.inserted = []
    view.busy = []
    view._insert = lambda text, tag=None: view.inserted.append(text)
    view._set_busy = lambda busy: view.busy.append(busy)
    view.after = lambda ms, func: None
    return view


def start_request(view, provider_id=None):
    """The state _request_llm_response() leaves behind, without a provider."""
    view._request_id += 1
    view._request_provider_id = provider_id
    view._cancel_event = threading.Event()
    view._partial = []
    view._set_busy(True)
    return view._request_id, view._cancel_event


@pytest.fixture
def panel_view(fake_thonny):
    from thonny.plugins.aia._panel import view as module

    return module


def test_cancel_before_first_token_frees_the_panel_at_once(panel_view):
    view = make_view(panel_view)
    start_request(view)

    view._on_cancel()

    assert view.busy[-1] is False  # Send is enabled again, no waiting for the worker
    assert view._cancel_event is None
    assert any("[cancelled]" in text for text in view.inserted)


def test_late_events_of_a_cancelled_request_are_dropped(panel_view):
    view = make_view(panel_view)
    request_id, _ = start_request(view)
    view._on_cancel()
    shown = list(view.inserted)

    view._events.put((request_id, "chunk", "late text"))
    view._events.put((request_id, "error", ("late error", False)))
    view._events.put((request_id, "done", " [cancelled]"))
    view._poll_events()

    assert view.inserted == shown
    assert view._conversation == []


def test_partial_answer_is_kept_when_cancelled_mid_stream(panel_view):
    view = make_view(panel_view)
    request_id, _ = start_request(view)
    view._events.put((request_id, "chunk", "Think about "))
    view._poll_events()

    view._on_cancel()

    assert view._conversation == [{"role": "assistant", "content": "Think about "}]


def test_next_request_after_cancel_runs_normally(panel_view):
    view = make_view(panel_view)
    old_id, _ = start_request(view)
    view._on_cancel()

    new_id, _ = start_request(view)
    view._events.put((old_id, "chunk", "stale"))
    view._events.put((new_id, "chunk", "fresh"))
    view._events.put((new_id, "done", ""))
    view._poll_events()

    assert "stale" not in view.inserted
    assert view._conversation == [{"role": "assistant", "content": "fresh"}]
    assert view.busy[-1] is False


def test_worker_closes_the_stream_of_a_cancelled_request(panel_view):
    view = make_view(panel_view)
    request_id, cancel_event = start_request(view)
    gate = threading.Event()
    closed = threading.Event()

    class SlowProvider:
        def stream_chat(self, system_prompt, messages, model):
            try:
                gate.wait(5)  # e.g. a slow service before the first token
                yield "first"
                yield "second"
            finally:
                closed.set()

    worker = threading.Thread(
        target=view._stream_in_background,
        args=(request_id, cancel_event, SlowProvider(), "openai", "", [], "m"),
        daemon=True,
    )
    worker.start()
    view._on_cancel()
    assert view.busy[-1] is False  # freed before the provider sent anything

    gate.set()
    worker.join(5)
    assert closed.is_set()
    events = []
    while not view._events.empty():
        events.append(view._events.get_nowait())
    assert ("chunk" not in {kind for _, kind, _ in events})
    view._poll_events()
    assert view._conversation == []


class _FakeText:
    def __init__(self):
        self.deleted = []

    def tag_ranges(self, tag):
        return ("2.5", "2.31") if tag == "loading_text" else ()

    def delete(self, start, end):
        self.deleted.append((start, end))

    def configure(self, **kwargs):
        pass


def test_local_loading_note_disappears_with_first_token(panel_view):
    view = make_view(panel_view)
    request_id, _ = start_request(view)
    view._messages_text = _FakeText()
    view._note = True  # a local model note is shown

    view._events.put((request_id, "chunk", "Ahoj"))
    view._poll_events()

    assert view._messages_text.deleted == [("2.5", "2.31")]
    assert view._note is False and view.inserted[-1] == "Ahoj"


def test_local_loading_note_disappears_on_cancel(panel_view):
    view = make_view(panel_view)
    start_request(view)
    view._messages_text = _FakeText()
    view._note = True

    view._on_cancel()

    assert view._messages_text.deleted == [("2.5", "2.31")] and view._note is False


class _LocalModel:
    """Ollama that reports the model as loaded after ``loaded_after`` checks."""

    def __init__(self, loaded_after, answer=("Ahoj",)):
        self.checks = 0
        self.loaded_after = loaded_after
        self.answer = answer
        self.released = threading.Event()

    def is_loaded(self, model):
        self.checks += 1
        return self.checks > self.loaded_after

    def stream_chat(self, system_prompt, messages, model):
        self.released.wait(5)
        yield from self.answer


def _drain(view):
    events = []
    while not view._events.empty():
        events.append(view._events.get_nowait())
    return events


def test_loaded_local_model_is_not_said_to_load(panel_view):
    """Jakub 2026-10-10: the model loaded at startup, yet the panel said it was loading."""
    view = make_view(panel_view)
    request_id, cancel_event = start_request(view)
    ollama = _LocalModel(loaded_after=0)
    ollama.released.set()

    view._stream_in_background(request_id, cancel_event, ollama, "ollama", "", [], "gpt-oss:20b")

    notes = [p for _, kind, p in _drain(view) if kind == "note"]
    assert notes == [panel_view.thinking_text()]  # the same note as at the start: nothing changes


def test_loading_note_turns_into_thinking_note_once_loaded(panel_view, monkeypatch):
    monkeypatch.setattr(panel_view, "LOAD_CHECK_S", 0.01)
    view = make_view(panel_view)
    request_id, cancel_event = start_request(view)
    ollama = _LocalModel(loaded_after=3)
    worker = threading.Thread(
        target=view._stream_in_background,
        args=(request_id, cancel_event, ollama, "ollama", "", [], "gpt-oss:20b"),
        daemon=True,
    )
    worker.start()
    for _ in range(500):
        if ollama.checks > 3:
            break
        threading.Event().wait(0.01)
    threading.Event().wait(0.05)
    ollama.released.set()
    worker.join(5)

    notes = [p for _, kind, p in list(view._events.queue) if kind == "note"]
    assert notes == [panel_view.waking_text(), panel_view.thinking_text()]
    view._messages_text = _FakeText()
    view._poll_events()
    assert "Ahoj" in view.inserted and view._note is False


def test_note_after_the_answer_is_ignored(panel_view):
    view = make_view(panel_view)
    request_id, cancel_event = start_request(view)
    view._messages_text = _FakeText()
    view._events.put((request_id, "chunk", "Ahoj"))
    view._events.put((request_id, "done", ""))
    view._poll_events()
    assert cancel_event.is_set()  # stops the watch of a loading model

    view._events.put((request_id, "note", panel_view.thinking_text()))
    view._poll_events()
    assert panel_view.thinking_text() not in view.inserted


def test_unreachable_ollama_shows_no_note(panel_view):
    view = make_view(panel_view)
    request_id, cancel_event = start_request(view)

    class _Down(_LocalModel):
        def is_loaded(self, model):
            raise ConnectionError("refused")

    ollama = _Down(loaded_after=0)
    ollama.released.set()
    view._stream_in_background(request_id, cancel_event, ollama, "ollama", "", [], "gpt-oss:20b")
    assert "note" not in {kind for _, kind, _ in _drain(view)}


def test_error_is_reported_to_the_model_but_not_shown(panel_view, fake_thonny):
    """After a runtime error AIA opens the conversation itself (Jakub 2026-10-10)."""

    class _Shell:
        class text:
            @staticmethod
            def get(start, end):
                assert (start, end) == ("command_io_start", "output_end")
                return "1\n2\n3\nTraceback (most recent call last):\nIndexError: list index out of range\n"

    fake_thonny.options["aia.enabled"] = True
    fake_thonny.show_view = lambda view_id, set_focus=True: None
    fake_thonny.get_view = lambda view_id: _Shell()
    view = make_view(panel_view)
    view.after = lambda ms, func: func()
    requests = []
    view._request_llm_response = lambda: requests.append(list(view._conversation))

    error = {"type_name": "IndexError", "message": "list index out of range", "lineno": 3}
    view.handle_toplevel_response({"user_exception": error})

    assert view.inserted == []  # nothing from the student in the panel
    (msg,) = requests[0]
    assert msg["role"] == "user" and msg["hidden"] is True
    assert "IndexError: list index out of range (line 3)" in msg["content"]
    assert "1\n2\n3\nTraceback" in msg["content"]  # the whole Shell output of the run
    assert "did not type this" in msg["content"]


def test_shell_output_is_limited_to_its_end(panel_view, fake_thonny):
    from thonny.plugins.aia import _common

    class _Shell:
        class text:
            get = staticmethod(lambda start, end: "x" * 10000 + "END")

    fake_thonny.get_view = lambda view_id: _Shell()
    out = _common.last_run_shell_output(fake_thonny)
    assert out.endswith("END") and len(out) <= _common.SHELL_OUTPUT_LIMIT + 4


class _Core:
    """AIACore with a ready provider whose answer never comes (the worker is not run)."""

    class _Provider:
        def frozen(self):
            return self

    def check_ready(self):
        return None

    def ready_problem(self):
        return None

    def get_current_provider_id(self):
        return "anthropic"

    def get_current_provider(self):
        return self._Provider()

    def get_current_model(self, provider):
        return "claude-haiku-5-5"


def test_thinking_note_follows_the_header_until_the_answer(panel_view):
    """Jakub 2026-10-13 (#74): "AIA: Thinking..." while waiting, replaced by the answer."""
    view = make_view(panel_view)
    view._insert = lambda text, tag=None: view.inserted.append((text, tag))
    view._core = _Core()
    view._stream_in_background = lambda *args: None
    view._conversation = [{"role": "user", "content": "Co je cyklus?"}]

    view._request_llm_response()

    assert view.inserted[-2][1] == "assistant_header"
    assert view.inserted[-1] == (panel_view.thinking_text(), "loading_text")
    assert view._note is True

    view._messages_text = _FakeText()
    view._events.put((view._request_id, "chunk", "Cyklus"))
    view._poll_events()
    assert view._messages_text.deleted == [("2.5", "2.31")]  # the note is gone
    assert view.inserted[-1] == ("Cyklus", None)


class _LocalCore(_Core):
    def get_current_provider_id(self):
        return "ollama"

    def get_current_model(self, provider):
        return "gpt-oss:20b"


@pytest.mark.parametrize("loading", [True, False])
def test_request_during_the_load_goes_on_waking_up(panel_view, monkeypatch, loading):
    """Jakub 2026-10-13 (#76): the whole load has one playful line, also after a message.

    In beta 4 a message sent during the load showed "Thinking..." until Ollama answered,
    then "Loading the local model...".
    """
    monkeypatch.setattr(panel_view, "is_loading", lambda: loading)
    view = make_view(panel_view)
    view._insert = lambda text, tag=None: view.inserted.append((text, tag))
    view._core = _LocalCore()
    view._stream_in_background = lambda *args: None
    view._conversation = [{"role": "user", "content": "Ahoj"}]

    view._request_llm_response()

    expected = panel_view.waking_text() if loading else panel_view.thinking_text()
    assert view.inserted[-1] == (expected, "loading_text")


def test_same_note_again_changes_nothing(panel_view):
    view = make_view(panel_view)
    view._insert = lambda text, tag=None: view.inserted.append((text, tag))
    view._messages_text = _FakeText()
    view._show_note(panel_view.waking_text())
    view._show_note(panel_view.waking_text())
    assert view.inserted == [(panel_view.waking_text(), "loading_text")]
    assert view._messages_text.deleted == []


def test_clear_asks_before_deleting_a_conversation(panel_view, monkeypatch):
    asked = []
    answer = []
    monkeypatch.setattr(
        panel_view.messagebox, "askyesno", lambda *a, **kw: asked.append(kw) or answer[-1]
    )
    view = make_view(panel_view)
    cleared = []
    view.clear_conversation = lambda: cleared.append(True)
    view._conversation = [{"role": "user", "content": "Ahoj"}]

    answer.append(False)
    view._on_clear()
    assert len(asked) == 1 and asked[0]["default"] == "no" and cleared == []

    answer.append(True)
    view._on_clear()
    assert cleared == [True]


def test_clear_of_an_empty_panel_does_not_ask(panel_view, monkeypatch):
    monkeypatch.setattr(panel_view.messagebox, "askyesno", lambda *a, **kw: pytest.fail("asked"))
    view = make_view(panel_view)
    cleared = []
    view.clear_conversation = lambda: cleared.append(True)

    view._on_clear()

    assert cleared == [True]


def test_failed_installation_is_reported_to_the_model(panel_view, fake_thonny):
    """Jakub 2026-10-13 (#74): AIA reacts to package installation errors too."""
    import types

    fake_thonny.show_view = lambda view_id, set_focus=True: None
    view = make_view(panel_view)
    requests = []
    view._request_llm_response = lambda: requests.append(list(view._conversation))
    event = types.SimpleNamespace(
        command="pip install --user nunpy",
        output="ERROR: No matching distribution found for nunpy",
    )

    view.handle_install_failure(event)

    assert view.inserted == []  # nothing from the student in the panel
    (msg,) = requests[0]
    assert msg["role"] == "user" and msg["hidden"] is True
    assert "pip install --user nunpy" in msg["content"]
    assert "No matching distribution found for nunpy" in msg["content"]
    assert "did not type this" in msg["content"]


def test_install_report_keeps_the_end_of_a_long_output(fake_thonny):
    from thonny.plugins.aia import _common

    report = _common.install_error_report("pip install x", "x" * 10000 + "ERROR: END")
    assert "ERROR: END" in report and len(report) < _common.SHELL_OUTPUT_LIMIT + 1000


def test_package_manager_reports_only_a_failed_install(fake_thonny, monkeypatch):
    import types

    from thonny.plugins import pip_gui

    events = []
    fake_thonny.event_generate = lambda sequence, **kwargs: events.append((sequence, kwargs))
    monkeypatch.setattr(pip_gui, "get_workbench", lambda: fake_thonny)
    report = pip_gui.PipDialog._report_failed_install

    def dlg(success=False, cancelled=False):
        return types.SimpleNamespace(
            success=success, cancelled=cancelled, stdout="Collecting nunpy ", stderr="ERROR: no"
        )

    report(None, "install", ["--user", "nunpy"], dlg(success=True))
    report(None, "install", ["--user", "nunpy"], dlg(cancelled=True))
    report(None, "uninstall", ["--yes", "nunpy"], dlg())
    assert events == []

    report(None, "install", ["--user", "nunpy"], dlg())
    ((sequence, kwargs),) = events
    assert sequence == "PackageInstallFailed"
    assert kwargs["command"] == "pip install --user nunpy"
    assert kwargs["output"] == "Collecting nunpy ERROR: no"


def test_installation_error_goes_to_the_panel_only_while_aia_is_on(fake_thonny):
    from thonny.plugins.aia._panel import switching

    got = []

    class _View:
        def handle_install_failure(self, event):
            got.append(event)

    fake_thonny.get_view = lambda view_id: _View()
    fake_thonny.options["aia.enabled"] = False
    switching.install_switching_logic(fake_thonny)
    (handler,) = fake_thonny.handlers["PackageInstallFailed"]

    handler("off")
    fake_thonny.options["aia.enabled"] = True
    handler("on")

    assert got == ["on"]


class _IdleText:
    """The few tk.Text calls of the messages area, on a list of (text, tag)."""

    def __init__(self):
        self.parts = []

    def get(self, start, end):
        return "".join(text for text, _ in self.parts)

    def insert(self, index, text, tag=None):
        if index == "1.0":
            self.parts.insert(0, (text, tag))
        else:
            self.parts.append((text, tag))

    def delete(self, start, end):
        self.parts = []

    def configure(self, **kw):
        pass

    def see(self, index):
        pass

    def window_create(self, index, window=None, **kw):
        self.parts.append((window, "window"))


class _Bar:
    def __init__(self):
        self.options = {}

    def configure(self, **kw):
        self.options.update(kw)

    def start(self, interval):
        self.options["running"] = True

    def stop(self):
        self.options["running"] = False


def make_idle_view(module):
    view = module.AIAView.__new__(module.AIAView)
    view._messages_text = _IdleText()
    view._idle = False
    view._idle_text = None
    view._idle_timer = None
    view.scheduled = []
    view.after = lambda ms, func: view.scheduled.append(func) or func
    view.after_cancel = lambda timer: view.scheduled.remove(timer)
    view._progress = _Bar()
    return view


def test_empty_panel_shows_a_playful_line_in_the_conversation_font(panel_view):
    view = make_idle_view(panel_view)
    view._show_idle()
    [(text, tag)] = view._messages_text.parts
    assert text in panel_view.idle_texts()
    assert tag == "system_text"  # colour only: the tag sets no font (_configure_tags)
    assert "AI Mentor Assistant" not in text

    view._insert("\nYou: ", "user_header")  # the first message replaces it, at the top
    assert view._messages_text.parts == [("You: ", "user_header")]


def test_clear_shows_another_playful_line(panel_view):
    view = make_idle_view(panel_view)
    view._show_idle()
    first = view._idle_text
    view._insert("\nYou: hi\n")
    view._abort_current_request = lambda: None
    view._conversation = []
    view.clear_conversation()
    assert view._idle_text in panel_view.idle_texts() and view._idle_text != first


def test_empty_panel_says_when_the_local_model_loads(panel_view, monkeypatch):
    view = make_idle_view(panel_view)
    loading = [True]
    monkeypatch.setattr(panel_view, "is_loading", lambda: loading[0])
    view._show_idle()
    assert view._messages_text.parts == [(panel_view.waking_text(), "system_text")]

    view.scheduled.pop()()  # still loading: checks again
    assert view._idle_text == panel_view.waking_text() and view.scheduled
    loading[0] = False
    view.scheduled.pop()()
    assert view._idle_text in panel_view.idle_texts()
    assert len(view._messages_text.parts) == 1


def test_a_load_starting_after_the_panel_shows_is_noticed(panel_view, monkeypatch):
    """The keeper starts loading a second after Thonny, the panel may be there already."""
    view = make_idle_view(panel_view)
    loading = [False]
    monkeypatch.setattr(panel_view, "is_loading", lambda: loading[0])
    view._show_idle()
    assert view._idle_text in panel_view.idle_texts()
    loading[0] = True
    view.scheduled.pop()()
    assert view._messages_text.parts == [(panel_view.waking_text(), "system_text")]
    assert len(view.scheduled) == 1  # one check at a time
    view._insert("You: hi")
    view.scheduled.pop()()  # the conversation started: no more checks
    assert view.scheduled == []


def test_an_answer_of_the_local_model_tells_the_keeper(panel_view, monkeypatch):
    """A keeper that gave up on the load (model too big, not pulled) loads again (#80)."""
    answered = []
    monkeypatch.setattr(panel_view, "model_answered", lambda: answered.append(True))
    view = make_view(panel_view)
    view._messages_text = _FakeText()
    for provider_id in ("anthropic", "ollama"):
        request_id, _ = start_request(view, provider_id)
        view._events.put((request_id, "chunk", "Ahoj"))
        view._events.put((request_id, "done", ""))
        view._poll_events()
    assert answered == [True]

    request_id, _ = start_request(view, "ollama")
    view._events.put((request_id, "error", ("The model x does not fit", True)))
    view._poll_events()
    assert answered == [True]


def _tagged_view(panel_view):
    view = make_view(panel_view)
    view.tagged = []
    view._insert = lambda text, tag=None: view.tagged.append((text, tag))
    return view


def test_a_rejected_key_says_what_the_assistant_needs_with_a_link(panel_view):
    """Jakub 2026-11-12, #117: a refused key in the panel, one click to the options."""
    from thonny.plugins.aia._common import friendly_error, error_opens_settings

    class AuthenticationError(Exception):
        status_code = 401

    exc = AuthenticationError("secret sk-ant-xyz")
    view = _tagged_view(panel_view)
    request_id, _ = start_request(view, "anthropic")
    view._events.put(
        (request_id, "error", (friendly_error(exc, "anthropic", "m"), error_opens_settings(exc)))
    )
    view._poll_events()

    shown = "".join(text for text, _ in view.tagged)
    assert "API key is not valid" in shown and "local model" in shown and "sk-" not in shown
    assert ("Set up the AI assistant", "settings_link") in view.tagged
    assert view.busy[-1] is False


def test_a_connection_error_has_no_link(panel_view):
    from thonny.plugins.aia._common import error_opens_settings, friendly_error

    exc = ConnectionError("no network")
    assert not error_opens_settings(exc, "openai")
    view = _tagged_view(panel_view)
    request_id, _ = start_request(view, "openai")
    view._events.put((request_id, "error", (friendly_error(exc, "openai"), False)))
    view._poll_events()
    assert all(tag != "settings_link" for _, tag in view.tagged)


def test_no_provider_gives_the_needs_text_with_a_link(panel_view, fake_thonny):
    fake_thonny.options["aia.provider"] = ""
    view = _tagged_view(panel_view)
    view._core = panel_view.AIACore()
    view._core.register_providers()

    view._request_llm_response()

    assert ("\nThe AI assistant needs an API key or a local model.", "error_text") in view.tagged
    assert ("Set up the AI assistant", "settings_link") in view.tagged
    assert view._cancel_event is None  # no request started


def test_the_link_opens_the_ai_assistant_options(panel_view, fake_thonny, monkeypatch):
    opened = []
    monkeypatch.setattr(
        fake_thonny, "show_options", lambda page=None: opened.append(page), raising=False
    )
    view = make_view(panel_view)
    view._messages_text = _FakeText()
    view._open_settings()
    assert opened == ["aia"]


# --- the local model that the application prepares (cortex-zamestnani#120) ---


def test_empty_panel_shows_the_preparation(panel_view, fake_thonny, local_models_ready):
    fake_thonny.options.update({"aia.provider": "ollama", "general.language": "en_US"})
    local_models_ready.phase = "downloading"
    local_models_ready.extra = {"stage": "model", "done": 3.2e9, "total": 12.8e9, "eta": 360, "resumed": True}
    view = make_idle_view(panel_view)
    view._show_idle()
    (text, tag), (_nl, _), (bar, kind) = view._messages_text.parts
    assert text == (
        "The assistant is getting ready... 3.2 GB of 12.8 GB, about 6 min left\n"
        "The download continues where it stopped."
    )
    assert kind == "window" and bar.options["value"] == 250 and bar.options["mode"] == "determinate"

    local_models_ready.extra["done"] = 6.4e9
    view.scheduled.pop()()  # the progress follows
    assert view._messages_text.parts[-1][0].options["value"] == 500

    local_models_ready.phase = "ready"
    local_models_ready.extra = {"just_prepared": True}
    view.scheduled.pop()()
    assert view._messages_text.parts == [("The assistant is ready.", "system_text")]


def test_empty_panel_links_a_model_that_is_not_downloaded(panel_view, fake_thonny, local_models_ready):
    fake_thonny.options.update({"aia.provider": "ollama"})
    local_models_ready.phase = "consent"
    view = make_idle_view(panel_view)
    view._show_idle()
    assert view._messages_text.parts == [
        ("The local model is not downloaded yet.", "system_text"),
        ("\n", None),
        (panel_view.settings_link_text(), "settings_link"),
    ]


def test_another_provider_keeps_the_playful_line(panel_view, fake_thonny, local_models_ready):
    fake_thonny.options.update({"aia.provider": "anthropic"})
    local_models_ready.phase = "downloading"
    view = make_idle_view(panel_view)
    view._show_idle()
    assert view._idle_text in panel_view.idle_texts()
