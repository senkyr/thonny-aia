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
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from thonny import get_workbench
from thonny.languages import tr
from thonny.ui_utils import AutomaticPanedWindow, scrollbar_style

from thonny.plugins.aia._common import (
    AIACore,
    error_opens_settings,
    error_report,
    friendly_error,
    install_error_report,
    interface_language,
    last_run_shell_output,
    prepare_messages,
    settings_link_text,
    snapshot_editor_context,
)
from thonny.plugins.aia.local_model import is_loading, model_answered
from thonny.plugins.aia.prompts import build_system_prompt

logger = logging.getLogger(__name__)

POLL_MS = 50
SHELL_SETTLE_MS = 300  # the Shell draws the output of a run a little after the run ends
LOAD_CHECK_S = 2.0  # while the local model loads, how often to ask Ollama whether it is in memory


# Shown after "AIA: " until the first part of the answer arrives, with every provider (Jakub
# 2026-10-13, cortex-zamestnani#74): until then the panel showed nothing and looked stuck.
def thinking_text() -> str:
    return tr("Thinking...")


# Grey hint in the empty input (Jakub 2026-10-13, cortex-zamestnani#74)
def input_hint_text() -> str:
    return tr("Write a message to the assistant...")


# The empty panel shows one of these, in the font of the conversation, instead of the title and
# instructions it had until beta 3 (Jakub 2026-10-13, cortex-zamestnani#75: "Asistent
# odpočívá...", "Asistent lelkuje...", "Asistent je na obědě...", they may take turns).
def idle_texts() -> List[str]:
    return [
        tr("The assistant is resting..."),
        tr("The assistant is loafing around..."),
        tr("The assistant is out for lunch..."),
        tr("The assistant is having a coffee..."),
        tr("The assistant is reading a book..."),
        tr("The assistant is looking out of the window..."),
        tr("The assistant is stretching..."),
    ]


# While the local model loads at startup (35-55 s on CPU), the empty panel says so: the load
# starts with Thonny, not with the first message (Jakub 2026-10-13, cortex-zamestnani#75).
# A request sent while the model is not in memory shows it after "AIA: " in place of
# thinking_text() (_report_local_model), so the whole load has one playful line (Jakub
# 2026-10-13, cortex-zamestnani#76: in beta 4 it changed to "Loading the local model...").
# Only while the model is really not loaded (Jakub 2026-10-10: with the model loaded at
# startup a note on every request made it look as if it loaded only now).
def waking_text() -> str:
    return tr("The assistant is waking up...")


IDLE_CHECK_MS = 1000  # how often the empty panel asks whether the local model still loads


# Background and text of the panel follow Thonny's UI theme, like the built-in Assistant it
# replaces and the other side views (Jakub 2026-10-12, cortex-zamestnani#71: in a dark theme
# the panel stayed white). The labels take colours of the syntax theme, which are readable on
# its background - unless the syntax theme is light and the UI dark or the other way round
# (Thonny does not tie them); then FALLBACK_COLORS of the panel's lightness apply.
TAG_COLORS = {  # tag -> syntax theme tag whose foreground it takes
    "user_header": "builtin",
    "assistant_header": "string",
    "system_text": "comment",
    "loading_text": "comment",
    "error_text": "stderr",  # the colour of an error in the Shell
    "settings_link": "builtin",  # like the student's label: readable, not the error's colour
}
FALLBACK_COLORS = {
    # light: the colours of beta 1
    False: {
        "user_header": "#1a5276",
        "assistant_header": "#117a65",
        "system_text": "#7f8c8d",
        "loading_text": "#7f8c8d",
        "error_text": "#c0392b",
        "settings_link": "#1a5276",
    },
    # dark: those of the Default Dark syntax theme
    True: {
        "user_header": "#A9B1C9",
        "assistant_header": "#8DC76F",
        "system_text": "#D4D44E",
        "loading_text": "#D4D44E",
        "error_text": "#EB5B83",
        "settings_link": "#A9B1C9",
    },
}
BORDER_MIX = 0.4  # border of the input: this much of the text colour, the rest background


def is_dark(color, rgb) -> bool:
    r, g, b = (c / 65535 for c in rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5


def theme_colors(ui_text, syntax_options, rgb):
    """Colours of the panel.

    ``ui_text`` holds background and foreground of the UI theme's "Text" style (empty if
    the theme has none), ``syntax_options`` the syntax theme (theme tag -> Tk options),
    ``rgb`` turns a Tk colour into (r, g, b) in 0-65535, like ``winfo_rgb``. Returns the
    options of both Text widgets, the foreground of each tag and the border colour of the
    input (None without theme colours).
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

    mixed = [round(f * BORDER_MIX + b * (1 - BORDER_MIX)) // 257 for f, b in zip(rgb(fg), rgb(bg))]
    return widget, tags, "#%02x%02x%02x" % tuple(mixed)



class AIAView(ttk.Frame):
    """Chat panel that provides AI-powered programming assistance."""

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

        self._build_ui()
        self._apply_theme()
        # Thonny applies a new UI theme and then the syntax theme, so this event covers both
        get_workbench().bind("SyntaxThemeChanged", self._apply_theme, True)

    def destroy(self):
        get_workbench().unbind("SyntaxThemeChanged", self._apply_theme)
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
            self._messages_text.tag_configure(tag, foreground=color)
        if "system_text" in tags:
            self._input_text.tag_configure("placeholder", foreground=tags["system_text"])

    def _idle_content(self):
        """(text, link to the options, progress 0-1 / "busy" / None) of the empty panel."""
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
                return text, False, status.get("done", 0) / status["total"]
            return text, False, "busy"
        if phase in (lm.CHECKING, lm.STARTING):
            return waking_text(), False, None
        problem = local_ui.problem(status) if phase else None
        if problem:
            return problem[0], problem[1], None
        if phase == lm.READY and status.get("just_prepared"):
            text = local_ui.ready_text()
            return (text + "\n" + waking_text() if is_loading() else text), False, None
        if is_loading():
            return waking_text(), False, None
        if self._idle_text in idle_texts():
            return self._idle_text, False, None  # the playful line stays until Clear
        return random.choice(idle_texts()), False, None

    def _show_idle(self, playful_again: bool = False):
        """The empty panel: a playful line, that the local model is loading, or how its
        preparation goes."""
        if self._messages_text.get("1.0", "end-1c"):
            return
        if playful_again and self._idle_text in idle_texts():
            # another playful line after Clear
            self._idle_text = random.choice([t for t in idle_texts() if t != self._idle_text])
        text, link, progress = self._idle_content()
        self._idle_text = text
        self._idle_key = (text, link, progress)
        self._idle = True

        def fill():
            self._messages_text.insert("end", text, "system_text")
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
        if getattr(self, "_progress", None) is None:
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

    def _insert(self, text: str, tag: Optional[str] = None):
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
        """The hint shows in the empty input while it does not have the focus."""
        text = self._input_text
        try:
            focused = self.focus_get() is text
        except (KeyError, tk.TclError):  # focus in a widget Tk cannot name (e.g. a menu)
            focused = False
        if self._placeholder or focused or text.get("1.0", "end-1c"):
            return
        self._placeholder = True
        self._edit_input(lambda: text.insert("1.0", input_hint_text(), "placeholder"))

    def _hide_placeholder(self, event=None):
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
        self._send_button.configure(state="disabled" if busy else "normal")
        self._cancel_button.configure(state="normal" if busy else "disabled")
        self._input_text.configure(state="disabled" if busy else "normal")
        if not busy:
            self._input_text.focus_set()

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
        report = error_report(error_info, last_run_shell_output(get_workbench()))
        self._conversation.append({"role": "user", "content": report, "hidden": True})
        self._request_llm_response()

    def handle_install_failure(self, event):
        """PackageInstallFailed from the package manager: react like to a runtime error."""
        self._abort_current_request()
        get_workbench().show_view("AIAView", set_focus=False)
        report = install_error_report(getattr(event, "command", ""), getattr(event, "output", ""))
        self._conversation.append({"role": "user", "content": report, "hidden": True})
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
        text = self._input_text.get("1.0", "end-1c").strip()
        if not text:
            return
        self._input_text.delete("1.0", "end")
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
        self._insert("\n" + tr("You") + ": ", "user_header")
        self._insert(f"{text}\n")

    def _abort_current_request(self):
        """Stop showing the running request (if any) and keep its partial answer."""
        if self._cancel_event is None:
            return
        self._cancel_event.set()
        self._request_id += 1  # drop its remaining events
        self._finish(" [" + tr("cancelled") + "]")

    def _request_llm_response(self):
        """Collect everything on the UI thread, then stream in a worker thread."""
        problem = self._core.ready_problem()
        if problem:
            self._insert_problem(*problem)
            self._insert("\n")
            return

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
        self._insert("\nAIA: ", "assistant_header")
        # While the keeper loads the model, the empty panel said "waking up": go on with it
        # right away, not with "Thinking..." until Ollama answers (_report_local_model)
        self._show_note(waking_text() if provider_id == "ollama" and is_loading() else thinking_text())

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
            put((request_id, "error", (message, error_opens_settings(e, provider_id))))
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
            # thinking_text() is shown already, unless the keeper's load ended just now
            put((request_id, "note", thinking_text()))
            return
        put((request_id, "note", waking_text()))

        def watch():
            while not cancel_event.wait(LOAD_CHECK_S):
                if request_id != self._request_id:
                    return  # superseded or finished
                try:
                    if provider.is_loaded(model):
                        put((request_id, "note", thinking_text()))
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
                        self._show_note(payload)
                elif kind == "chunk":
                    self._hide_loading()
                    self._partial.append(payload)
                    self._insert(payload)
                elif kind == "done":
                    self._finish(payload)
                    if self._request_provider_id == "ollama":
                        model_answered()  # the keeper may have given up on the load
                elif kind == "error":
                    self._hide_loading()
                    self._insert_problem(*payload)
                    self._finish("")
        except queue.Empty:
            pass

        if self._cancel_event is not None:
            self.after(POLL_MS, self._poll_events)
        else:
            self._polling = False

    def _show_note(self, text: str):
        """Show a note after "AIA: " in place of the previous one."""
        if getattr(self, "_note", False) and getattr(self, "_note_text", None) == text:
            return
        self._hide_loading()
        self._insert(text, "loading_text")
        self._note = True
        self._note_text = text

    def _hide_loading(self):
        """Remove the note once the answer (or an error, or Cancel) takes its place."""
        if not getattr(self, "_note", False):
            return
        self._note = False
        text = self._messages_text
        ranges = text.tag_ranges("loading_text")
        text.configure(state="normal")
        for i in range(len(ranges) - 2, -1, -2):
            text.delete(ranges[i], ranges[i + 1])
        text.configure(state="disabled")

    def _finish(self, suffix: str):
        self._hide_loading()
        if suffix:
            self._insert(suffix)
        self._insert("\n")
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
        self._messages_text.configure(state="normal")
        self._messages_text.delete("1.0", "end")
        self._messages_text.configure(state="disabled")
        self._idle = False
        self._show_idle(playful_again=True)
