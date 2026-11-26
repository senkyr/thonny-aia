# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""AIAView - custom chat panel for Thonny without the Chat API (4.x, 5.0.x).

Replaces the built-in rule-based AssistantView when AIA is enabled.

Threading: the LLM request runs in a worker thread that never touches Tk. It
only puts events into a queue.Queue, which the UI thread drains with
``after()`` polling. Every event carries a request id, so events from a
superseded (cancelled) request are dropped.
"""

import logging
import queue
import random
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from thonny import get_workbench
from thonny.languages import tr
from thonny.ui_utils import AutomaticPanedWindow, scrollbar_style

from thonny.plugins.aia._common import (
    PROVIDERS_NEEDING_KEY,
    AIACore,
    error_opens_settings,
    error_report,
    friendly_error,
    install_error_report,
    interface_language,
    is_rejected_key,
    last_run_shell_output,
    prepare_messages,
    rejected_key_text,
    settings_link_text,
    snapshot_editor_context,
)
from thonny.plugins.aia.local_model import is_loading, model_answered
from thonny.plugins.aia.prompts import build_system_prompt

logger = logging.getLogger(__name__)

clock = time.monotonic  # the morning routine's clock (tests replace it)
POLL_MS = 50
SHELL_SETTLE_MS = 300  # the Shell draws the output of a run a little after the run ends
LOAD_CHECK_S = 2.0  # while the local model loads, how often to ask Ollama whether it is in memory


# Shown after "AIA: " until the first part of the answer arrives, with every provider (Jakub
# 2026-10-13, cortex-zamestnani#74): until then the panel showed nothing and looked stuck. In
# the third person like the other playful lines (Jakub 2026-11-26: "asistent přemýšlí"; until
# then "Thinking...", cs "Přemýšlím...").
def thinking_text() -> str:
    return tr("The assistant is thinking...")


# When the thinking takes longer, these follow it, more slowly than the warm-up below (Jakub
# 2026-11-26: the waiting lines should have a dynamics of their own; a 6 s period all along)
def thinking_texts() -> List[str]:
    return [
        tr("The assistant is thinking hard..."),
        tr("The assistant is weighing its words..."),
        tr("The assistant is leafing through the documentation..."),
        tr("The assistant is scratching its head..."),
        tr("The assistant is drawing it on a napkin..."),
        tr("The assistant is almost there..."),
    ]


# The first answer of a conversation takes much longer than the next ones (the SDK is imported,
# the connection opened, the provider has nothing cached), so it gets lines of its own that
# take turns every few seconds: the assistant, away in the empty panel (idle_texts), is coming
# (Jakub 2026-11-26: "protahování, rozcvičování, dodělávání domácího úkolu, venčení psa,
# vaření si čaje").
def warmup_texts() -> List[str]:
    return [
        tr("The assistant is stretching..."),
        tr("The assistant is warming up..."),
        tr("The assistant is finishing its homework..."),
        tr("The assistant is walking the dog..."),
        tr("The assistant is making tea..."),
        tr("The assistant is putting on its slippers..."),
        tr("The assistant is looking for its glasses..."),
        tr("The assistant is sharpening its pencils..."),
        tr("The assistant is on its way..."),
    ]


WARMUP_TURN_MS = 3000  # a warm-up line takes the next one's place
THINKING_TURN_MS = 6000  # "thinking", then each of its variants, stays this long
WAKING_TURN_MS = 5000  # a line of the local model's morning routine stays this long
WAKING_CHECK_MS = 500  # how often the note of a loading model follows the routine's clock
WAKING_GAP_S = 3.0  # a routine not asked for this long belongs to an earlier load


# Shown after "AIA: " when the answer ends without any text (the provider's filter stopped it,
# or the model only thought): until beta 9 the line after "AIA: " stayed empty.
def no_answer_text() -> str:
    return tr("The assistant gave no answer. Try asking again.")


# Grey hint in the empty input (Jakub 2026-10-13, cortex-zamestnani#74)
def input_hint_text() -> str:
    return tr("Write a message to the assistant...")


# In place of the hint while the input is off because the assistant cannot answer, not playful
# (Jakub 2026-11-25): something is to be set in Tools > Options (the panel has the link) ...
def blocked_hint_text() -> str:
    return tr("You cannot write to the assistant until it has a valid API key or a local model.")


# ... or it only has to get ready (the local model being prepared)
def not_ready_hint_text() -> str:
    return tr("You cannot write to the assistant until it is ready.")


# The empty panel shows one of these, in the font of the conversation, instead of the title and
# instructions it had until beta 3 (Jakub 2026-10-13, cortex-zamestnani#75: "Asistent
# odpočívá...", "Asistent lelkuje...", "Asistent je na obědě...", they may take turns). The
# assistant is there for the student but away for now, so that "Write a message to the
# assistant" calls it back (Jakub 2026-11-26: "asistent je na obědě", "na procházce", "si
# odskočil"); it does not change until Clear. Stretching moved to warmup_texts().
def idle_texts() -> List[str]:
    return [
        tr("The assistant is resting..."),
        tr("The assistant is loafing around..."),
        tr("The assistant is out for lunch..."),
        tr("The assistant is having a coffee..."),
        tr("The assistant is reading a book..."),
        tr("The assistant is looking out of the window..."),
        tr("The assistant is out for a walk..."),
        tr("The assistant has stepped out for a moment..."),
        tr("The assistant is watering the plants..."),
        tr("The assistant is taking a nap..."),
    ]


# While the local model loads at startup (35-55 s on CPU), the empty panel says so: the load
# starts with Thonny, not with the first message (Jakub 2026-10-13, cortex-zamestnani#75).
# A request sent while the model is not in memory shows it after "AIA: " in place of
# thinking_text() (_report_local_model), so the whole load is one waking up (Jakub 2026-10-13,
# cortex-zamestnani#76: in beta 4 it changed to "Loading the local model..."). Only while the
# model is really not loaded (Jakub 2026-10-10: with the model loaded at startup a note on
# every request made it look as if it loaded only now).
def waking_text() -> str:
    return tr("The assistant is waking up...")


# A long load goes on with the assistant's morning routine, every WAKING_TURN_MS, in the empty
# panel and after "AIA: " alike, by one clock (_waking_line), so a message sent in the middle
# of it does not start it again (Jakub 2026-11-26: "čistí si zuby", "vybírá ponožky").
def morning_texts() -> List[str]:
    return [
        tr("The assistant is brushing its teeth..."),
        tr("The assistant is choosing its socks..."),
        tr("The assistant is yawning..."),
        tr("The assistant is rubbing its eyes..."),
        tr("The assistant is making its bed..."),
        tr("The assistant is combing its hair..."),
        tr("The assistant is having breakfast..."),
        tr("The assistant is opening the curtains..."),
    ]


# What the worker tells the panel about the wait (its "note" events): the local model is not in
# memory yet (waking up and the morning routine, _waking_line), or the provider works on the answer
# (the lines of the request's wait, see _show_waiting)
WAKING = "waking"
THINKING = "thinking"

IDLE_CHECK_MS = 1000  # how often the empty panel asks whether the local model still loads
PROBLEM_CHECK_MS = 1000  # how often a shown problem asks whether it still holds
STATE_CHECK_MS = 1000  # how often the panel asks whether the assistant can answer at all


# Background and text of the panel follow Thonny's UI theme, like the built-in Assistant it
# replaces and the other side views (Jakub 2026-10-12, cortex-zamestnani#71: in a dark theme
# the panel stayed white). The labels take colours of the syntax theme, which are readable on
# its background - unless the syntax theme is light and the UI dark or the other way round
# (Thonny does not tie them); then FALLBACK_COLORS of the panel's lightness apply.
TAG_COLORS = {  # tag -> syntax theme tag whose foreground it takes
    "user_header": "builtin",
    "assistant_header": "string",
    "error_text": "stderr",  # the colour of an error in the Shell
    "settings_link": "builtin",  # like the student's label: readable, not the error's colour
}
FALLBACK_COLORS = {
    # light: the colours of beta 1
    False: {
        "user_header": "#1a5276",
        "assistant_header": "#117a65",
        "error_text": "#c0392b",
        "settings_link": "#1a5276",
    },
    # dark: those of the Default Dark syntax theme
    True: {
        "user_header": "#A9B1C9",
        "assistant_header": "#8DC76F",
        "error_text": "#EB5B83",
        "settings_link": "#A9B1C9",
    },
}
# The assistant's own lines (the empty panel, waiting for the answer, the local model's
# preparation) are blue and the hint in the input is light grey, in every theme (Jakub
# 2026-11-26: until then both took the syntax theme's comment colour, red in IDLE Classic,
# yellow in Default Dark; red is for errors only). Not from the syntax theme, which has no
# colour of that meaning: a blue of the panel's lightness, made lighter or darker until it is
# readable on the background, and a mix of the text colour into the background.
PLAYFUL_BLUE = {False: "#1f66b8", True: "#79aaf0"}
PLAYFUL_CONTRAST = 4.5  # WCAG AA for normal text
HINT_MIX = 0.45  # hint in the input: at least this much of the text colour, the rest background
HINT_CONTRAST = 3.0  # ... and more of it until this contrast (Clean Dark's text is dim itself)
BORDER_MIX = 0.4  # border of the input: this much of the text colour, the rest background
PLAYFUL_TAGS = ("system_text", "loading_text")


def is_dark(color, rgb) -> bool:
    r, g, b = (c / 65535 for c in rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5


def _luminance(rgb16) -> float:
    """Relative luminance (WCAG) of (r, g, b) in 0-65535."""

    def linear(c):
        c = c / 65535
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb16)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(rgb_a, rgb_b) -> float:
    la, lb = sorted((_luminance(rgb_a), _luminance(rgb_b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(fg16, bg16, share: float) -> str:
    """``share`` of ``fg16`` and the rest of ``bg16`` (both 0-65535) as "#rrggbb"."""
    mixed = [round(f * share + b * (1 - share)) // 257 for f, b in zip(fg16, bg16)]
    return "#%02x%02x%02x" % tuple(mixed)


def readable(color, bg, rgb, minimum: float = PLAYFUL_CONTRAST) -> str:
    """``color``, made lighter on a dark ``bg`` or darker on a light one until the contrast
    reaches ``minimum`` (a blue on the dark blue of Tomorrow Night Blue, for example)."""
    bg16 = rgb(bg)
    toward = (65535,) * 3 if is_dark(bg, rgb) else (0, 0, 0)
    color16 = rgb(color)
    for step in range(11):
        candidate = mix(toward, color16, step / 10)
        if contrast(rgb(candidate), bg16) >= minimum:
            return candidate
    return mix(toward, color16, 1.0)


def theme_colors(ui_text, syntax_options, rgb):
    """Colours of the panel.

    ``ui_text`` holds background and foreground of the UI theme's "Text" style (empty if
    the theme has none), ``syntax_options`` the syntax theme (theme tag -> Tk options),
    ``rgb`` turns a Tk colour into (r, g, b) in 0-65535, like ``winfo_rgb``. Returns the
    options of both Text widgets, the foreground of each tag (of the messages, and
    "placeholder" of the input) and the border colour of the input (None without theme
    colours).
    """
    syntax_text = syntax_options.get("TEXT", {})
    bg = ui_text.get("background") or syntax_text.get("background")
    fg = ui_text.get("foreground") or syntax_text.get("foreground")
    if not (bg and fg):
        return {}, {}, None

    widget = {"background": bg, "foreground": fg, "insertbackground": fg}
    dark = is_dark(bg, rgb)
    syntax_bg = syntax_text.get("background")
    if syntax_bg and is_dark(syntax_bg, rgb) == dark:
        tags = {
            tag: syntax_options.get(theme_tag, {}).get("foreground") or fg
            for tag, theme_tag in TAG_COLORS.items()
        }
        sel = syntax_options.get("sel", {})
        if sel.get("background"):
            widget["selectbackground"] = sel["background"]
            widget["inactiveselectbackground"] = sel["background"]
        if sel.get("foreground"):
            widget["selectforeground"] = sel["foreground"]
    else:
        tags = dict(FALLBACK_COLORS[dark])

    playful = readable(PLAYFUL_BLUE[dark], bg, rgb)
    for tag in PLAYFUL_TAGS:
        tags[tag] = playful
    tags["placeholder"] = hint_color(rgb(fg), rgb(bg))
    return widget, tags, mix(rgb(fg), rgb(bg), BORDER_MIX)


def hint_color(fg16, bg16) -> str:
    """Grey of the hint: HINT_MIX of the text colour, more until HINT_CONTRAST."""
    share = HINT_MIX
    while share < 1.0:
        color = mix(fg16, bg16, share)
        rgb16 = tuple(int(color[i : i + 2], 16) * 257 for i in (1, 3, 5))
        if contrast(rgb16, bg16) >= HINT_CONTRAST:
            return color
        share = min(1.0, share + 0.05)
    return mix(fg16, bg16, 1.0)


class AIAView(ttk.Frame):
    """Chat panel that provides AI-powered programming assistance."""

    # Why the assistant does not answer is in the panel once (_show_problem)
    _problem = None  # (message, link to the options) of the problem shown
    _problem_readiness = False  # AIACore.ready_problem() says it, no request failed with it
    _problem_settings = None  # AIACore.settings_signature() when it was shown
    _problem_timer = None

    # Whether the assistant can answer at all (_watch_state): while it cannot, the input and
    # Send are off (Jakub 2026-11-25: a message to an assistant known not to work stayed in the
    # panel unanswered, also after the key was fixed)
    _refused = None  # AIACore.settings_signature() whose key the provider refused
    _key_checked = None  # the signature whose key was last checked
    _key_token = 0  # the key check whose answer counts
    _key_results = None  # queue of the key checks' answers
    _state_timer = None
    _last_message = None  # what the student wrote last, until a request takes it
    _request_message = None  # what the student wrote for the running request
    _request_signature = None  # the settings of the running request
    _placeholder_text = None  # the hint shown in the input

    # The lines while waiting for the answer (_show_waiting)
    _warmup = False  # the request waits for the first answer of the conversation
    _note_kind = None  # WAKING or THINKING: what the note shown says
    _turn_timer = None  # the next line of the wait takes the place of the shown one
    _turn_lines = None  # the lines still to come in this round
    _waking = None  # (line, shown since, last asked) of the local model's load (_waking_line)
    _waking_round = None  # the morning routine's lines still to come in this round

    def __init__(self, master):
        super().__init__(master)

        self._core = AIACore()
        self._core.register_providers()
        self._conversation: List[Dict[str, str]] = []

        self._events: "queue.Queue" = queue.Queue()
        self._request_id = 0  # id of the request whose events are shown
        self._request_provider_id = None  # provider of that request
        self._cancel_event: Optional[threading.Event] = None
        self._partial: List[str] = []
        self._polling = False
        self._note = False  # a note (thinking / the local model waking up) is in the messages area
        self._note_text = None

        self._key_results = queue.Queue()

        self._build_ui()
        self._apply_theme()
        # Thonny applies a new UI theme and then the syntax theme, so this event covers both
        get_workbench().bind("SyntaxThemeChanged", self._apply_theme, True)
        self._state_timer = self.after_idle(self._watch_state)

    def destroy(self):
        get_workbench().unbind("SyntaxThemeChanged", self._apply_theme)
        if self._state_timer is not None:
            self.after_cancel(self._state_timer)
            self._state_timer = None
        self._stop_turns()
        super().destroy()

    # --- UI ---

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # The answers above, the input below, with a border the user can move; in the default
        # layout the border runs at the height of the one above the Shell (quadrants.py)
        self.split = AutomaticPanedWindow(self, orient=tk.VERTICAL)
        self.split.grid(row=0, column=0, sticky="nsew")

        messages_frame = ttk.Frame(self.split)
        messages_frame.preferred_size_in_pw = None  # takes what the input leaves
        messages_frame.columnconfigure(0, weight=1)
        messages_frame.rowconfigure(0, weight=1)

        self._messages_text = tk.Text(
            messages_frame,
            wrap="word",
            state="disabled",
            borderwidth=0,
            padx=8,
            pady=8,
            spacing1=2,
            spacing3=2,
        )
        self._messages_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            messages_frame, command=self._messages_text.yview, style=scrollbar_style("Vertical")
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._messages_text.configure(yscrollcommand=scrollbar.set)

        self._configure_tags()
        self._idle = False  # only the empty panel's line is in the messages area
        self._idle_text = None
        self._idle_timer = None
        self._show_idle()

        # the pane keeps its height when the panel grows (AutomaticPanedWindow), the input inside
        # it stretches to the pane
        self.input_pane = ttk.Frame(self.split)
        self.input_pane.preferred_size_in_pw = 0  # set below and by set_input_height()
        self.input_pane.columnconfigure(0, weight=1)
        self.input_pane.rowconfigure(0, weight=1)
        input_frame = ttk.Frame(self.input_pane)
        input_frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=(2, 4))
        input_frame.columnconfigure(0, weight=1)
        input_frame.rowconfigure(0, weight=1)
        self._input_frame = input_frame

        # height=1 is only the minimum: the input stretches to the pane, at least to the height
        # of the button column, so its edges line up with Send and Clear
        # the border is a highlight, so that its colour can follow the theme
        self._input_text = tk.Text(
            input_frame, height=1, wrap="word", borderwidth=0, highlightthickness=1
        )
        self._input_text.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._input_text.bind("<Return>", self._on_enter)
        self._input_text.bind("<FocusIn>", self._hide_placeholder, True)
        self._input_text.bind("<FocusOut>", self._show_placeholder, True)
        self._placeholder = False
        self._show_placeholder()

        button_frame = ttk.Frame(input_frame)
        button_frame.grid(row=0, column=1, sticky="ns")

        self._send_button = ttk.Button(button_frame, text=tr("Send"), width=9, command=self._on_send)
        self._send_button.pack(side="top", pady=(0, 2))

        self._cancel_button = ttk.Button(
            button_frame, text=tr("Cancel"), width=9, command=self._on_cancel, state="disabled"
        )
        self._cancel_button.pack(side="top", pady=(0, 2))

        ttk.Button(button_frame, text=tr("Clear"), width=9, command=self._on_clear).pack(side="top")

        self.input_pane.preferred_size_in_pw = self.input_minimum()
        self.split.add(messages_frame, minsize=self.input_minimum())
        self.split.add(self.input_pane, minsize=self.input_minimum())
        self.after_idle(self._start_quadrants)

    def input_minimum(self) -> int:
        """The smallest height of the input pane: the input with its three buttons."""
        self._input_frame.update_idletasks()
        return self._input_frame.winfo_reqheight() + 6  # pady of the input frame

    def set_input_height(self, height: int):
        """Height of the input pane; the answers above get the rest."""
        # AutomaticPanedWindow gives the last pane its preferred size minus the sash before it
        height = max(int(height), self.input_minimum()) + int(str(self.split.cget("sashwidth")))
        # also when only the actual height is off: Tk gives the extra space of a growing panel to
        # its last pane, and AutomaticPanedWindow does not always restore it
        sash = int(str(self.split.cget("sashwidth")))
        actual = self.input_pane.winfo_height()
        if self.input_pane.preferred_size_in_pw != height or (actual > 1 and actual != height - sash):
            self.input_pane.preferred_size_in_pw = height
            self.split._check_restore_preferred_sizes()

    def _start_quadrants(self):
        # home_widget (the view's place in a notebook) exists only after the constructor
        from thonny.plugins.aia._panel.quadrants import Quadrants

        try:
            self._quadrants = Quadrants(get_workbench(), self)
        except Exception:
            logger.debug("Could not set up the quadrants", exc_info=True)

    def _configure_tags(self):
        bold_font = ("TkDefaultFont", 9, "bold")

        # colours come from the theme (_apply_theme); system_text (the empty panel) keeps the
        # font of the conversation
        self._messages_text.tag_configure("user_header", font=bold_font)
        self._messages_text.tag_configure("assistant_header", font=bold_font)

        # One click opens Tools > Options > AI Assistant (Jakub 2026-11-12, cortex-zamestnani#117)
        text = self._messages_text
        text.tag_configure("settings_link", underline=True)
        text.tag_bind("settings_link", "<Button-1>", self._open_settings)
        text.tag_bind("settings_link", "<Enter>", lambda e: text.configure(cursor="hand2"))
        text.tag_bind("settings_link", "<Leave>", lambda e: text.configure(cursor=""))

    def _open_settings(self, event=None):
        self._messages_text.configure(cursor="")
        get_workbench().show_options("aia")

    def _insert_problem(self, message: str, settings: bool):
        """A problem in the error colour; with a link to the options when it is fixed there."""
        self._insert("\n" + message, "error_text")
        if settings:
            self._insert("\n")
            self._insert(settings_link_text(), "settings_link")

    def _show_problem(self, message: str, settings: bool, readiness: bool = False):
        """Why the assistant does not answer: in the panel once, at its end, until it can.

        Until beta 9 every message and every failed run of the student's program added the same
        lines again ("The AI assistant needs an API key or a local model."), and a panel without
        a provider filled up with them (Jakub 2026-11-25). Now the same problem stays where it
        is, or moves below what the student wrote since, and another one takes its place. It
        goes away when the assistant can answer again (_check_problem, the first part of an
        answer).

        ``readiness``: AIACore.ready_problem() says it (no provider, no key, the local model
        not prepared) and no request was sent.
        """
        if readiness and self._idle_says(message):
            return  # the empty panel says it already, with the local model's progress
        if self._problem == (message, settings) and self._problem_is_last():
            self._problem_readiness = readiness
            self._schedule_problem_check()
            return
        self._remove_problem(idle=False)
        self._insert("\n" + message, ("error_text", "problem"))
        if settings:
            self._insert("\n", "problem")
            self._insert(settings_link_text(), ("settings_link", "problem"))
        self._insert("\n", "problem")
        self._problem = (message, settings)
        self._problem_readiness = readiness
        self._problem_settings = self._core.settings_signature()
        self._schedule_problem_check()

    def _idle_says(self, message: str) -> bool:
        """Whether the empty panel says this already (how the local model is being prepared)."""
        if not getattr(self, "_idle", False):
            return False
        text = self._idle_content()[0]
        return text == message or text.startswith(message + "\n")

    def _problem_is_last(self) -> bool:
        text = self._messages_text
        ranges = text.tag_ranges("problem")
        return bool(ranges) and bool(text.compare(ranges[-1], "==", "end-1c"))

    def _remove_problem(self, idle: bool = True):
        """Take the shown problem out of the panel; an emptied panel shows its line again."""
        if self._problem_timer is not None:
            self.after_cancel(self._problem_timer)
            self._problem_timer = None
        if self._problem is None:
            return
        self._problem = None
        text = self._messages_text
        ranges = text.tag_ranges("problem")

        def remove():
            for i in range(len(ranges) - 2, -1, -2):
                text.delete(ranges[i], ranges[i + 1])

        self._edit_messages(remove)
        if idle:
            self._show_idle()  # only when nothing else is left in the panel

    def _schedule_problem_check(self):
        if self._problem_timer is not None:
            self.after_cancel(self._problem_timer)
        self._problem_timer = self.after(PROBLEM_CHECK_MS, self._check_problem)

    def _check_problem(self):
        """While a problem is shown: does it still hold?

        What ready_problem() says is asked again: it goes away with the choice of a provider,
        with a key or with the local model prepared, and its text follows the preparation. A
        failed request (a refused key, no connection) stays until the settings change or an
        answer comes.
        """
        self._problem_timer = None
        if self._problem is None:
            return
        if self._cancel_event is None:  # a running request appends to the panel's end
            current = self._core.ready_problem()
            if current is not None:
                self._show_problem(*current, readiness=True)
                if self._problem is None:
                    return
            elif (
                self._problem_readiness
                or self._core.settings_signature() != self._problem_settings
            ):
                self._remove_problem()
                return
        self._schedule_problem_check()

    def _known_problem(self, signature=None):
        """(message, link to the options, readiness) when the assistant is known not to
        answer, else None. UI thread.

        Known: what ready_problem() says (no provider, no key, the local model not prepared),
        and a key the provider refused (_watch_state's check or a request) while the settings
        stay the same.
        """
        problem = self._core.ready_problem()
        if problem is not None:
            return problem[0], problem[1], True
        if self._refused is not None:
            if self._refused == (signature or self._core.settings_signature()):
                return rejected_key_text(), True, False
        return None

    def _watch_state(self):
        """Every second while the panel lives: can the assistant answer?

        Until beta 10 the panel found out only from a request: with a key the provider refuses
        (a typo, an expired key) the empty panel showed its playful line, the student's first
        message got the error and stayed there unanswered, also after the key was fixed (Jakub
        2026-11-25). Now the key is checked when the panel starts and after every change of the
        settings (the provider's model listing, like the options page), what is known shows in
        the panel and the input is off until it is fixed.
        """
        self._state_timer = self.after(STATE_CHECK_MS, self._watch_state)
        signature = self._core.settings_signature()
        self._take_key_results(signature)
        if signature != self._key_checked:
            self._key_checked = signature
            self._start_key_check(signature)
        if self._cancel_event is not None:
            return  # the running request says how it went
        known = self._known_problem(signature)
        if known is not None and self._problem is None and not self._idle_covers():
            self._show_problem(known[0], known[1], readiness=known[2])
        self._update_input()

    def _idle_covers(self) -> bool:
        """The empty panel shows the application's local model in every phase itself."""
        from thonny.plugins.aia import local_ui

        return bool(getattr(self, "_idle", False)) and local_ui.handles(get_workbench())

    def _start_key_check(self, signature):
        """Ask the provider in a worker thread whether it accepts the key of ``signature``."""
        self._key_token += 1
        provider_id, key = signature[0], signature[1]
        if provider_id not in PROVIDERS_NEEDING_KEY or not key:
            return
        provider = self._core.get_current_provider()
        if provider is None or self._core.ready_problem() is not None:
            return
        asking = provider.frozen()  # the key read here, on the UI thread
        token = self._key_token
        results = self._key_results

        def work():
            try:
                asking.list_models()
                refused = False
            except Exception as e:
                # no connection, a timeout: unknown, the first request will tell
                logger.info("AIA: could not check the key of %s", provider_id, exc_info=True)
                refused = is_rejected_key(e)
            results.put((token, signature, refused))

        threading.Thread(target=work, daemon=True).start()

    def _take_key_results(self, signature):
        while True:
            try:
                token, checked, refused = self._key_results.get_nowait()
            except queue.Empty:
                return
            if token == self._key_token and refused and checked == signature:
                self._refused = checked

    def _update_input(self, focus: bool = False):
        """Input and Send are off while a request runs or the assistant cannot answer."""
        busy = self._cancel_event is not None
        known = None if busy else self._known_problem()
        off = busy or known is not None
        state = "disabled" if off else "normal"
        self._send_button.configure(state=state)
        self._cancel_button.configure(state="normal" if busy else "disabled")
        self._input_text.configure(state=state)
        if focus and not off:
            self._input_text.focus_set()
        self._refresh_placeholder(known)

    @staticmethod
    def _hint_for(known) -> str:
        if known is None:
            return input_hint_text()
        return blocked_hint_text() if known[1] else not_ready_hint_text()

    def _input_focused(self) -> bool:
        try:
            return self.focus_get() is self._input_text
        except (KeyError, tk.TclError):  # focus in a widget Tk cannot name (e.g. a menu)
            return False

    def _refresh_placeholder(self, known):
        """The hint follows whether the student can write: an empty disabled input says why not."""
        text = self._input_text
        if self._placeholder:
            if known is None and self._input_focused():
                self._hide_placeholder()  # on again with the focus in it: the student types
                return
            hint = self._hint_for(known)
            if self._placeholder_text != hint:

                def replace():
                    text.delete("1.0", "end")
                    text.insert("1.0", hint, "placeholder")

                self._edit_input(replace)
                self._placeholder_text = hint
        elif known is not None and not text.get("1.0", "end-1c"):
            self._show_placeholder()

    def _return_message(self):
        """The student's message of a request the provider refused goes back to the input.

        The assistant did not see it; it waits there until the key is fixed, instead of staying
        in the panel unanswered.
        """
        text, self._request_message = self._request_message, None
        if not text or not self._conversation:
            return
        if self._conversation[-1] != {"role": "user", "content": text}:
            return
        self._conversation.pop()
        self._forget_unanswered_reports()
        self._edit_messages(lambda: self._messages_text.delete("message_start", "end-1c"))
        self._hide_placeholder()
        if not self._input_text.get("1.0", "end-1c").strip():

            def put_back():
                self._input_text.delete("1.0", "end")
                self._input_text.insert("1.0", text)

            self._edit_input(put_back)

    def _apply_theme(self, event=None):
        """Colours of the active themes; also called when a theme changes."""
        from thonny.codeview import get_syntax_options_for_tag

        style = ttk.Style()
        ui_text = {key: style.lookup("Text", key) for key in ("background", "foreground")}
        names = {"TEXT", "sel", *TAG_COLORS.values()}
        options = {name: get_syntax_options_for_tag(name) for name in names}
        widget, tags, border = theme_colors(ui_text, options, self.winfo_rgb)

        for text in (self._messages_text, self._input_text):
            text.configure(**widget)
        if border:
            self._input_text.configure(highlightbackground=border, highlightcolor=border)
        for tag, color in tags.items():
            if tag == "placeholder":
                self._input_text.tag_configure(tag, foreground=color)
            else:
                self._messages_text.tag_configure(tag, foreground=color)

    def _idle_content(self):
        """(text, link to the options, progress 0-1 / "busy" / None, tag) of the empty panel.

        The tag is "system_text" (blue) for the assistant's own lines and "error_text" (red)
        for why the local model cannot answer (Jakub 2026-11-26: red is for errors).
        """
        from thonny.plugins.aia import local_ui
        from thonny.plugins.aia import local_models as lm

        status = lm.get_manager().status() if local_ui.handles(get_workbench()) else {}
        phase = status.get("phase")
        if phase == lm.DOWNLOADING:
            # the application prepares the local model (cortex-zamestnani#120): its progress
            # takes the place of the playful line
            text = local_ui.preparing_text(status)
            if status.get("resumed"):
                text += "\n" + local_ui.resumed_text()
            if status.get("stage") == "model" and status.get("total"):
                return text, False, status.get("done", 0) / status["total"], "system_text"
            return text, False, "busy", "system_text"
        if phase in (lm.CHECKING, lm.STARTING):
            return self._waking_line(), False, None, "system_text"
        problem = local_ui.problem(status) if phase else None
        if problem:
            return problem[0], problem[1], None, "error_text"
        if phase == lm.READY and status.get("just_prepared"):
            text = local_ui.ready_text()
            text = text + "\n" + self._waking_line() if is_loading() else text
            return text, False, None, "system_text"
        if is_loading():
            return self._waking_line(), False, None, "system_text"
        self._waking = None  # loaded: a later load wakes up from the start
        if self._idle_text in idle_texts():
            # the playful line stays until Clear
            return self._idle_text, False, None, "system_text"
        return random.choice(idle_texts()), False, None, "system_text"

    def _show_idle(self, playful_again: bool = False):
        """The empty panel: a playful line, that the local model is loading, or how its
        preparation goes."""
        if self._messages_text.get("1.0", "end-1c"):
            return
        if playful_again and self._idle_text in idle_texts():
            # another playful line after Clear
            self._idle_text = random.choice([t for t in idle_texts() if t != self._idle_text])
        text, link, progress, tag = self._idle_content()
        self._idle_text = text
        self._idle_key = (text, link, progress, tag)
        self._idle = True

        def fill():
            self._messages_text.insert("end", text, tag)
            if link:
                self._messages_text.insert("end", "\n")
                self._messages_text.insert("end", settings_link_text(), "settings_link")
            if progress is not None:
                bar = self._progress_bar()
                if progress == "busy":
                    bar.configure(mode="indeterminate")
                    bar.start(50)
                else:
                    bar.stop()
                    bar.configure(mode="determinate", value=progress * 1000)
                self._messages_text.insert("end", "\n")
                self._messages_text.window_create("end", window=bar, pady=6)

        self._edit_messages(fill)
        self._schedule_idle_check()

    def _progress_bar(self):
        if getattr(self, "_progress", None) is None or not self._progress.winfo_exists():
            self._progress = ttk.Progressbar(self._messages_text, maximum=1000, length=240)
        return self._progress

    def _schedule_idle_check(self):
        if self._idle_timer is not None:
            self.after_cancel(self._idle_timer)
        self._idle_timer = self.after(IDLE_CHECK_MS, self._update_idle)

    def _update_idle(self):
        """While the panel is empty: say when the local model starts or stops loading.

        The load starts a second after Thonny, or after switching to the local model in the
        Options, maybe while the panel already shows its playful line.
        """
        self._idle_timer = None
        if not self._idle:
            return  # the conversation started; clear_conversation() checks again
        if self._idle_content() != self._idle_key:
            self._hide_idle()
            self._show_idle()  # schedules the next check
        else:
            self._schedule_idle_check()

    def _hide_idle(self):
        if self._idle:
            self._idle = False
            self._edit_messages(lambda: self._messages_text.delete("1.0", "end"))

    def _edit_messages(self, change):
        self._messages_text.configure(state="normal")
        change()
        self._messages_text.configure(state="disabled")

    def _insert(self, text: str, tag=None):
        """Append to the messages; ``tag`` is a tag name or a tuple of them."""
        self._hide_idle()
        if not self._messages_text.get("1.0", "end-1c"):
            text = text.lstrip("\n")  # the first message starts at the top
        self._messages_text.configure(state="normal")
        if tag:
            self._messages_text.insert("end", text, tag)
        else:
            self._messages_text.insert("end", text)
        self._messages_text.configure(state="disabled")
        self._messages_text.see("end")

    def _show_placeholder(self, event=None):
        """The hint shows in the empty input while it does not have the focus; why the student
        cannot write shows also with the focus."""
        text = self._input_text
        if self._placeholder or text.get("1.0", "end-1c"):
            return
        known = None if self._cancel_event is not None else self._known_problem()
        if known is None and self._input_focused():
            return
        hint = self._hint_for(known)
        self._placeholder = True
        self._placeholder_text = hint
        self._edit_input(lambda: text.insert("1.0", hint, "placeholder"))

    def _hide_placeholder(self, event=None):
        if event is not None and str(self._input_text.cget("state")) == "disabled":
            return  # a click into the disabled input: why it is off stays there
        if self._placeholder:
            self._placeholder = False
            self._edit_input(lambda: self._input_text.delete("1.0", "end"))

    def _edit_input(self, change):
        """Change the input also while it is disabled (an answer is coming)."""
        state = self._input_text.cget("state")
        self._input_text.configure(state="normal")
        change()
        self._input_text.configure(state=state)

    def _set_busy(self, busy: bool):
        self._update_input(focus=not busy)

    # --- Event handling ---

    def handle_toplevel_response(self, msg):
        """Bound to ToplevelResponse while AIA is enabled. Auto-explain errors."""
        if not get_workbench().get_option("aia.enabled"):
            return

        error_info = msg.get("user_exception")
        if not error_info:
            return

        # A new error supersedes an answer that is still streaming
        self._abort_current_request()
        get_workbench().show_view("AIAView", set_focus=False)
        # The Shell may still be drawing the output of the run
        self.after(SHELL_SETTLE_MS, lambda: self._react_to_error(error_info))

    def _react_to_error(self, error_info):
        """AIA opens the conversation itself: the report goes to the model, not to the panel."""
        if self._cancel_event is not None:
            return  # the student sent a message in the meantime
        self._add_report(error_report(error_info, last_run_shell_output(get_workbench())))
        self._request_llm_response()

    def _add_report(self, report: str):
        """An automatic report for the model, not shown in the panel.

        A report that got no answer (its request failed or was cancelled) gives way to the newer
        one: until beta 9 they piled up, and the first request that got through carried them all.
        """
        self._forget_unanswered_reports()
        self._conversation.append({"role": "user", "content": report, "hidden": True})

    def _forget_unanswered_reports(self):
        while self._conversation and self._conversation[-1].get("hidden"):
            self._conversation.pop()

    def handle_install_failure(self, event):
        """PackageInstallFailed from the package manager: react like to a runtime error."""
        self._abort_current_request()
        get_workbench().show_view("AIAView", set_focus=False)
        self._add_report(
            install_error_report(getattr(event, "command", ""), getattr(event, "output", ""))
        )
        self._request_llm_response()

    def _on_enter(self, event):
        """Enter sends the message, Shift+Enter inserts a newline."""
        if event.state & 0x0001:  # Shift
            return None
        self._on_send()
        return "break"

    def _on_send(self):
        if self._cancel_event is not None:
            return  # a request is running
        if self._placeholder:
            return  # only the hint is in the input
        if self._known_problem() is not None:
            return  # Enter works in a disabled input too; the panel says what is missing
        text = self._input_text.get("1.0", "end-1c").strip()
        if not text:
            return
        # also while the input is still off: the settings were fixed less than a second ago and
        # the message sent back by a refused key is sent now
        self._edit_input(lambda: self._input_text.delete("1.0", "end"))
        self._add_user_message(text)
        self._request_llm_response()

    def _on_clear(self):
        """Clear asks first: the conversation cannot be brought back (Jakub 2026-10-13, #74)."""
        if (self._conversation or self._cancel_event is not None) and not messagebox.askyesno(
            tr("Clear the conversation?"),
            tr("The whole conversation with AIA will be deleted. This cannot be undone."),
            default=messagebox.NO,
            master=self,
        ):
            return
        self.clear_conversation()

    def _on_cancel(self):
        # Free the panel right away: the worker may be blocked before the first
        # token (e.g. a slow service) and only sees the event when the
        # next chunk arrives - it closes the stream then, its events are dropped.
        self._abort_current_request()

    # --- Conversation management ---

    def _add_user_message(self, text: str):
        self._conversation.append({"role": "user", "content": text})
        self._hide_idle()
        # where the message starts: a refused key sends it back to the input (_return_message)
        self._messages_text.mark_set("message_start", "end-1c")
        self._messages_text.mark_gravity("message_start", "left")
        self._insert("\n" + tr("You") + ": ", "user_header")
        self._insert(f"{text}\n")
        self._last_message = text

    def _abort_current_request(self):
        """Stop showing the running request (if any) and keep its partial answer."""
        if self._cancel_event is None:
            return
        self._cancel_event.set()
        self._request_id += 1  # drop its remaining events
        self._finish(" [" + tr("cancelled") + "]")

    def _request_llm_response(self):
        """Collect everything on the UI thread, then stream in a worker thread."""
        self._request_message, self._last_message = self._last_message, None
        signature = self._core.settings_signature()
        problem = self._known_problem(signature)
        if problem:
            # The assistant is not set up and saw nothing: a report of this moment is not sent
            # later with the first message either
            self._forget_unanswered_reports()
            self._show_problem(problem[0], problem[1], readiness=problem[2])
            return
        self._request_signature = signature
        if self._problem is not None and self._problem_readiness:
            self._remove_problem(idle=False)  # it can answer now, _check_problem did not run yet

        wb = get_workbench()
        provider_id = self._core.get_current_provider_id()
        provider = self._core.get_current_provider()
        model = self._core.get_current_model(provider)

        ctx = snapshot_editor_context(wb)
        system_prompt = build_system_prompt(
            mentoring_level=wb.get_option("aia.mentoring_level"),
            current_code=ctx["code"],
            current_file_name=ctx["file_name"],
            imported_files=ctx["imported_files"],
            local=provider_id == "ollama",
            language=interface_language(wb.get_option("general.language")),
        )
        api_messages = prepare_messages(self._conversation)

        self._request_id += 1
        self._request_provider_id = provider_id
        self._cancel_event = threading.Event()
        self._partial = []
        self._set_busy(True)
        self._hide_idle()
        # where the lines of this request start: one that fails before its answer leaves only
        # the problem in the panel (_drop_request_lines)
        self._messages_text.mark_set("request_start", "end-1c")
        self._messages_text.mark_gravity("request_start", "left")
        self._insert("\nAIA: ", "assistant_header")
        self._warmup = not any(m["role"] == "assistant" for m in self._conversation)
        # While the keeper loads the model, the empty panel said "waking up": go on with it
        # right away, not with the thinking lines until Ollama answers (_report_local_model)
        self._show_waiting(WAKING if provider_id == "ollama" and is_loading() else THINKING)

        threading.Thread(
            target=self._stream_in_background,
            args=(
                self._request_id,
                self._cancel_event,
                provider.frozen(),  # settings read here, on the UI thread
                provider_id,
                system_prompt,
                api_messages,
                model,
            ),
            daemon=True,
        ).start()
        self._start_polling()

    def _stream_in_background(
        self, request_id, cancel_event, provider, provider_id, system_prompt, api_messages, model
    ):
        """Worker thread: no Tk calls, only queue puts."""
        put = self._events.put
        stream = None
        try:
            if provider_id == "ollama":
                self._report_local_model(request_id, cancel_event, provider, model)
            stream = provider.stream_chat(system_prompt, api_messages, model)
            for chunk_text in stream:
                if cancel_event.is_set():
                    put((request_id, "done", " [" + tr("cancelled") + "]"))
                    return
                put((request_id, "chunk", chunk_text))
            put((request_id, "done", ""))
        except Exception as e:
            logger.exception("AIA: error during chat completion")
            message = friendly_error(e, provider_id, model)
            refused = provider_id in PROVIDERS_NEEDING_KEY and is_rejected_key(e)
            put((request_id, "error", (message, error_opens_settings(e, provider_id), refused)))
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    close()  # releases the HTTP stream on cancel
                except Exception:
                    logger.debug("Error closing stream", exc_info=True)

    def _report_local_model(self, request_id, cancel_event, provider, model):
        """Worker thread: tell the UI whether the model still loads; if it does, watch it."""
        put = self._events.put
        try:
            loaded = provider.is_loaded(model)
        except Exception:
            # Ollama not reachable: the chat request reports it in a moment
            logger.debug("AIA: could not ask Ollama for loaded models", exc_info=True)
            return
        if loaded:
            # the thinking lines go on already, unless the keeper's load ended just now
            put((request_id, "note", THINKING))
            return
        put((request_id, "note", WAKING))

        def watch():
            while not cancel_event.wait(LOAD_CHECK_S):
                if request_id != self._request_id:
                    return  # superseded or finished
                try:
                    if provider.is_loaded(model):
                        put((request_id, "note", THINKING))
                        return
                except Exception:
                    return

        threading.Thread(target=watch, daemon=True).start()

    def _start_polling(self):
        if not self._polling:
            self._polling = True
            self.after(POLL_MS, self._poll_events)

    def _poll_events(self):
        try:
            while True:
                request_id, kind, payload = self._events.get_nowait()
                if request_id != self._request_id:
                    continue  # superseded request
                if kind == "note":
                    if self._cancel_event is not None and not self._partial:  # not started yet
                        self._show_waiting(payload)
                elif kind == "chunk":
                    self._hide_loading()
                    self._remove_problem(idle=False)  # the assistant answers again
                    self._refused = None
                    self._partial.append(payload)
                    self._insert(payload)
                elif kind == "done":
                    if not payload and not self._partial:
                        self._hide_loading()
                        self._insert(no_answer_text(), "error_text")
                    self._finish(payload)
                    if self._request_provider_id == "ollama":
                        model_answered()  # the keeper may have given up on the load
                elif kind == "error":
                    self._hide_loading()
                    message, settings, *refused = payload
                    if self._partial:
                        # in the middle of an answer: it stays there, part of the conversation
                        self._insert_problem(message, settings)
                        self._finish("")
                    else:
                        self._drop_request_lines()
                        if refused and refused[0]:
                            # known now: the input goes off (_end_request) and the message
                            # waits in it until the key is fixed
                            self._refused = self._request_signature
                            self._return_message()
                        self._end_request()
                        self._show_problem(message, settings)
        except queue.Empty:
            pass

        if self._cancel_event is not None:
            self.after(POLL_MS, self._poll_events)
        else:
            self._polling = False

    def _show_waiting(self, kind: str):
        """The note after "AIA: " while the answer is coming; each wait has its own pace.

        WAKING: the local model is loading: waking up, then its morning routine every
        WAKING_TURN_MS, on the same clock as the empty panel (_waking_line).
        THINKING: the first answer of a conversation takes long (the assistant was away, see
        idle_texts()), so its warm-up lines take turns every WARMUP_TURN_MS from the start; a
        later answer starts with thinking_text(), then the thinking lines follow, each for
        THINKING_TURN_MS (Jakub 2026-11-26).
        """
        if self._note_kind == kind and getattr(self, "_note", False):
            return  # the same wait goes on
        self._stop_turns()
        self._note_kind = kind
        if kind == WAKING:
            self._show_note(self._waking_line())
            self._turn_timer = self.after(WAKING_CHECK_MS, self._take_turn)
            return
        self._waking = None  # the model is loaded: a later load wakes up from the start
        if self._warmup:
            self._show_note(self._next_line(warmup_texts()))
            self._turn_timer = self.after(WARMUP_TURN_MS, self._take_turn)
        else:
            self._show_note(thinking_text())
            self._turn_timer = self.after(THINKING_TURN_MS, self._take_turn)

    def _waking_line(self) -> str:
        """The line of the local model's load now: waking up, then the morning routine.

        One clock for the empty panel and the note after "AIA: ": a message sent in the middle
        of the load goes on with the line the panel showed. A routine nobody asked for longer
        than WAKING_GAP_S belongs to an earlier load and starts again.
        """
        now = clock()
        if self._waking is None or now - self._waking[2] > WAKING_GAP_S:
            self._waking = (waking_text(), now, now)
            self._waking_round = []
        line, since, _ = self._waking
        if now - since >= WAKING_TURN_MS / 1000:
            if not self._waking_round:
                self._waking_round = [t for t in morning_texts() if t != line]
                random.shuffle(self._waking_round)
            line, since = self._waking_round.pop(), now
        self._waking = (line, since, now)
        return line

    def _next_line(self, pool: List[str]) -> str:
        """The next line of a round through ``pool`` in random order, never the one shown."""
        if not self._turn_lines:
            shown = getattr(self, "_note_text", None) if getattr(self, "_note", False) else None
            self._turn_lines = [line for line in pool if line != shown]
            random.shuffle(self._turn_lines)
        return self._turn_lines.pop()

    def _take_turn(self):
        """The next line of the wait, while the answer has not started."""
        self._turn_timer = None
        if self._cancel_event is None or self._partial or not getattr(self, "_note", False):
            return
        if self._note_kind == WAKING:
            self._show_note(self._waking_line())
            self._turn_timer = self.after(WAKING_CHECK_MS, self._take_turn)
            return
        if self._note_kind != THINKING:
            return
        if self._warmup:
            self._show_note(self._next_line(warmup_texts()))
            self._turn_timer = self.after(WARMUP_TURN_MS, self._take_turn)
        else:
            self._show_note(self._next_line(thinking_texts()))
            self._turn_timer = self.after(THINKING_TURN_MS, self._take_turn)

    def _stop_turns(self):
        if self._turn_timer is not None:
            self.after_cancel(self._turn_timer)
            self._turn_timer = None
        self._turn_lines = None

    def _show_note(self, text: str):
        """Show a note after "AIA: " in place of the previous one."""
        if getattr(self, "_note", False) and getattr(self, "_note_text", None) == text:
            return
        self._remove_note()
        self._insert(text, "loading_text")
        self._note = True
        self._note_text = text

    def _hide_loading(self):
        """Remove the note once the answer (or an error, or Cancel) takes its place."""
        self._stop_turns()
        self._note_kind = None
        self._remove_note()

    def _remove_note(self):
        if not getattr(self, "_note", False):
            return
        self._note = False
        text = self._messages_text
        ranges = text.tag_ranges("loading_text")
        text.configure(state="normal")
        for i in range(len(ranges) - 2, -1, -2):
            text.delete(ranges[i], ranges[i + 1])
        text.configure(state="disabled")

    def _drop_request_lines(self):
        """Remove "AIA: " of a request that failed before its answer started."""
        self._edit_messages(lambda: self._messages_text.delete("request_start", "end-1c"))

    def _finish(self, suffix: str):
        self._hide_loading()
        if suffix:
            self._insert(suffix)
        self._insert("\n")
        self._end_request()

    def _end_request(self):
        full_response = "".join(self._partial)
        if full_response:
            self._conversation.append({"role": "assistant", "content": full_response})
        self._partial = []
        if self._cancel_event is not None:
            self._cancel_event.set()  # stops the watch of a loading local model
        self._cancel_event = None
        self._set_busy(False)

    def clear_conversation(self):
        """Forget the chat history (starts a fresh conversation)."""
        self._abort_current_request()
        self._conversation.clear()
        self._remove_problem(idle=False)
        self._messages_text.configure(state="normal")
        self._messages_text.delete("1.0", "end")
        self._messages_text.configure(state="disabled")
        self._idle = False
        self._show_idle(playful_again=True)
