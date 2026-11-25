# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""Why the assistant does not answer is in the panel once (Jakub 2026-11-25, beta 10).

Until beta 9 every message and every failed run added the same lines again. The tests use a
real tk.Text for the messages area, because the behaviour is about what the panel holds;
skipped where Tk cannot start.
"""

import queue
import threading
import tkinter as tk

import pytest

NEEDS = "The AI assistant needs an API key or a local model."
LINK = "Set up the AI assistant"
REJECTED = (
    "The AI assistant's API key is not valid. The assistant needs a valid API key or a "
    "local model."
)
OFFLINE = "Cannot connect to the AI service. Check the internet connection."


@pytest.fixture
def panel_view(fake_thonny):
    from thonny.plugins.aia._panel import view as module

    return module


class Core:
    """AIACore whose readiness and settings the test sets; the worker is not run."""

    class _Provider:
        def frozen(self):
            return self

    def __init__(self, problem=None):
        self.problem = problem
        self.settings = ("anthropic", "key", "claude-haiku-5-5", None)

    def ready_problem(self):
        return self.problem

    def settings_signature(self):
        return self.settings

    def get_current_provider_id(self):
        return "anthropic"

    def get_current_provider(self):
        return self._Provider()

    def get_current_model(self, provider):
        return "claude-haiku-5-5"


def make_view(module, root, core, fake_thonny):
    fake_thonny.options.setdefault("aia.provider", "anthropic")
    fake_thonny.options.setdefault("aia.mentoring_level", "socratic")
    view = module.AIAView.__new__(module.AIAView)
    view._messages_text = tk.Text(root, state="disabled")
    view._core = core
    view._conversation = []
    view._events = queue.Queue()
    view._request_id = 0
    view._request_provider_id = None
    view._cancel_event = None
    view._partial = []
    view._polling = False
    view._note = False
    view._note_text = None
    view._idle = False
    view._idle_text = None
    view._idle_timer = None
    view.timers = []
    view.after = lambda ms, func: view.timers.append(func) or func
    view.after_cancel = lambda timer: timer in view.timers and view.timers.remove(timer)
    view._set_busy = lambda busy: None
    view._stream_in_background = lambda *args: None  # the test sends the worker's events
    view._start_polling = lambda: None
    return view


def shown(view):
    return view._messages_text.get("1.0", "end-1c")


def say(view, text):
    view._add_user_message(text)
    view._request_llm_response()


def fail(view, message, settings=False):
    view._events.put((view._request_id, "error", (message, settings)))
    view._poll_events()


def answer(view, text):
    view._events.put((view._request_id, "chunk", text))
    view._events.put((view._request_id, "done", ""))
    view._poll_events()


def error_run(view):
    """What _react_to_error does after a failed run of the student's program."""
    view._add_report("[Automatic notice from Thonny] it stopped with NameError")
    view._request_llm_response()


def tick(view):
    """The pending check of the shown problem (PROBLEM_CHECK_MS later)."""
    checks = [t for t in view.timers if getattr(t, "__name__", "") == "_check_problem"]
    assert len(checks) == 1
    view.timers.remove(checks[0])
    checks[0]()


def test_failed_runs_without_a_provider_show_the_problem_once(panel_view, root, fake_thonny):
    view = make_view(panel_view, root, Core(problem=(NEEDS, True)), fake_thonny)

    for _ in range(5):
        error_run(view)

    assert shown(view) == NEEDS + "\n" + LINK + "\n"
    assert view._conversation == []  # the assistant saw nothing: no report is sent later


def test_the_problem_moves_below_what_the_student_wrote(panel_view, root, fake_thonny):
    view = make_view(panel_view, root, Core(problem=(NEEDS, True)), fake_thonny)

    say(view, "ahoj")
    say(view, "haló?")
    error_run(view)

    assert shown(view) == "You: ahoj\n\nYou: haló?\n\n" + NEEDS + "\n" + LINK + "\n"
    assert [m["content"] for m in view._conversation] == ["ahoj", "haló?"]


def test_the_problem_goes_away_when_a_provider_is_connected(panel_view, root, fake_thonny):
    core = Core(problem=(NEEDS, True))
    view = make_view(panel_view, root, core, fake_thonny)
    idle = []
    view._show_idle = lambda: idle.append(shown(view))
    error_run(view)

    tick(view)
    assert NEEDS in shown(view)  # still no provider: it stays

    core.problem = None
    tick(view)
    assert shown(view) == "" and idle == [""]  # the emptied panel shows its line again
    assert view.timers == []


def test_the_problem_goes_away_from_a_conversation_too(panel_view, root, fake_thonny):
    core = Core(problem=(NEEDS, True))
    view = make_view(panel_view, root, core, fake_thonny)
    say(view, "ahoj")

    core.problem = None
    tick(view)

    assert shown(view) == "You: ahoj\n"


def test_a_message_right_after_connecting_removes_the_problem(panel_view, root, fake_thonny):
    core = Core(problem=(NEEDS, True))
    view = make_view(panel_view, root, core, fake_thonny)
    say(view, "ahoj")

    core.problem = None  # the check has not run yet
    say(view, "už?")
    answer(view, "Ahoj!")

    assert shown(view) == "You: ahoj\n\nYou: už?\n\nAIA: Ahoj!\n"


def test_the_text_of_the_problem_follows_the_preparation(panel_view, root, fake_thonny):
    core = Core(problem=("The assistant is getting ready... 3.2 GB of 13.8 GB", False))
    view = make_view(panel_view, root, core, fake_thonny)
    say(view, "ahoj")

    core.problem = ("The assistant is getting ready... 6.4 GB of 13.8 GB", False)
    tick(view)

    assert shown(view) == "You: ahoj\n\nThe assistant is getting ready... 6.4 GB of 13.8 GB\n"


def test_a_request_that_keeps_failing_shows_its_error_once(panel_view, root, fake_thonny):
    view = make_view(panel_view, root, Core(), fake_thonny)

    for _ in range(3):
        error_run(view)
        assert "AIA: " in shown(view)  # it tries: "AIA: Thinking..."
        fail(view, OFFLINE)

    assert shown(view) == OFFLINE + "\n"
    assert len(view._conversation) == 1  # only the last report waits for the connection


def test_a_failed_request_keeps_the_students_message_above_the_error(panel_view, root, fake_thonny):
    view = make_view(panel_view, root, Core(), fake_thonny)

    say(view, "co je cyklus?")
    fail(view, REJECTED, True)
    say(view, "haló?")
    fail(view, REJECTED, True)

    assert shown(view) == (
        "You: co je cyklus?\n\nYou: haló?\n\n" + REJECTED + "\n" + LINK + "\n"
    )


def test_an_answer_removes_the_error_of_the_request_before(panel_view, root, fake_thonny):
    view = make_view(panel_view, root, Core(), fake_thonny)
    say(view, "co je cyklus?")
    fail(view, OFFLINE)

    say(view, "a teď?")
    assert OFFLINE in shown(view)  # until the answer starts
    answer(view, "Cyklus opakuje příkazy.")

    assert shown(view) == "You: co je cyklus?\n\nYou: a teď?\n\nAIA: Cyklus opakuje příkazy.\n"
    assert view.timers == []


def test_a_refused_key_stays_until_the_settings_change(panel_view, root, fake_thonny):
    core = Core()
    view = make_view(panel_view, root, core, fake_thonny)
    say(view, "ahoj")
    fail(view, REJECTED, True)

    tick(view)
    assert REJECTED in shown(view)  # ready_problem() knows nothing about a refused key

    core.settings = ("anthropic", "another key", "claude-haiku-5-5", None)
    tick(view)
    assert shown(view) == "You: ahoj\n"


def test_an_error_in_the_middle_of_an_answer_stays_in_the_conversation(panel_view, root, fake_thonny):
    view = make_view(panel_view, root, Core(), fake_thonny)
    say(view, "co je cyklus?")
    view._events.put((view._request_id, "chunk", "Cyklus"))
    view._poll_events()
    fail(view, OFFLINE)

    say(view, "a dál?")
    answer(view, "opakuje příkazy.")

    assert shown(view) == (
        "You: co je cyklus?\n\nAIA: Cyklus\n" + OFFLINE + "\n\nYou: a dál?\n\nAIA: opakuje příkazy.\n"
    )
    assert view._conversation[1] == {"role": "assistant", "content": "Cyklus"}


def test_an_answer_without_text_says_so(panel_view, root, fake_thonny):
    """A provider's filter stopped the answer, or the model only thought: until beta 9 the
    line after "AIA: " stayed empty."""
    view = make_view(panel_view, root, Core(), fake_thonny)
    say(view, "ahoj")
    view._events.put((view._request_id, "done", ""))
    view._poll_events()

    assert shown(view) == "You: ahoj\n\nAIA: The assistant gave no answer. Try asking again.\n"
    assert [m["role"] for m in view._conversation] == ["user"]


def test_another_problem_takes_the_place_of_the_shown_one(panel_view, root, fake_thonny):
    core = Core()
    view = make_view(panel_view, root, core, fake_thonny)
    say(view, "ahoj")
    fail(view, REJECTED, True)

    core.problem = (NEEDS, True)  # the provider was switched to "later" in the options
    tick(view)

    assert shown(view) == "You: ahoj\n\n" + NEEDS + "\n" + LINK + "\n"


def test_clear_removes_the_problem_and_its_check(panel_view, root, fake_thonny):
    view = make_view(panel_view, root, Core(problem=(NEEDS, True)), fake_thonny)
    view._show_idle = lambda playful_again=False: None
    error_run(view)

    view.clear_conversation()

    assert shown(view) == "" and view._problem is None and view.timers == []


def test_the_link_of_the_problem_is_clickable_text(panel_view, root, fake_thonny):
    view = make_view(panel_view, root, Core(problem=(NEEDS, True)), fake_thonny)
    error_run(view)

    text = view._messages_text
    start, end = (str(i) for i in text.tag_ranges("settings_link"))
    assert text.get(start, end) == LINK
    assert "error_text" in text.tag_names("1.0")


def test_the_empty_panel_keeps_saying_how_the_local_model_is_prepared(
    panel_view, root, fake_thonny, local_models_ready
):
    """The empty panel shows the download with its progress bar: a failed run does not replace
    it with the same words in red."""
    fake_thonny.options.update({"aia.provider": "ollama", "general.language": "en_US"})
    local_models_ready.phase = "downloading"
    local_models_ready.extra = {"stage": "model", "done": 3.2e9, "total": 12.8e9, "eta": 360}
    view = make_view(panel_view, root, panel_view.AIACore(), fake_thonny)
    view._core.register_providers()
    view._progress = type("Bar", (), {"winfo_exists": lambda s: True, "stop": lambda s: None,
                                      "configure": lambda s, **kw: None})()
    view._messages_text.window_create = lambda index, **kw: None
    view._show_idle()
    before = shown(view)
    assert before.startswith("The assistant is getting ready... 3.2 GB of 12.8 GB")

    error_run(view)

    assert shown(view) == before and view._idle is True and view._problem is None


def test_settings_signature_changes_with_the_key_and_the_provider(fake_thonny):
    from thonny.plugins.aia._common import AIACore

    fake_thonny.options.update({"aia.provider": "anthropic", "aia.ollama_host": ""})
    fake_thonny.secrets["aia_anthropic_api_key"] = "sk-one"
    core = AIACore()
    core.register_providers()
    first = core.settings_signature()

    assert core.settings_signature() == first
    fake_thonny.secrets["aia_anthropic_api_key"] = "sk-two"
    second = core.settings_signature()
    assert second != first
    fake_thonny.options["aia.provider"] = "ollama"
    assert core.settings_signature() != second
    fake_thonny.options["aia.provider"] = ""
    assert core.settings_signature() == ("", None, None, None)
