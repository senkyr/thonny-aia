# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""Configuration page for AIA (Tools > Options > AI Assistant).

The AI choice is the welcome dialog's choice (Jakub 2026-10-10: the options had other items
than the dialog and a model choice that was quietly dropped): the same list in the same
order, the same notes, the API key on the page. With an API key the page also offers the
model (Jakub 2026-10-13, cortex-zamestnani#75): the models the provider lists for the key,
asked in a worker thread, or the provider's usual models when it cannot be asked. The local
model too (Jakub 2026-10-16, cortex-zamestnani#80): the chat models Ollama has on this
computer, asked the same way; when Ollama does not answer, only the default and the chosen
model are offered. The Claude Code demo provider is not offered (Jakub 2026-10-10: "To demo tam nemá co dělat."):
it is reachable only by editing configuration.ini.
"""

import copy
import logging
import queue
import threading
import tkinter as tk
import types
import webbrowser
from tkinter import ttk

from thonny import first_run, get_workbench, ui_utils
from thonny.languages import tr
from thonny.config_ui import ConfigurationPage

from thonny.plugins.aia._common import (
    AIACore,
    MODEL_CHOICE_PROVIDERS,
    is_rejected_key,
    model_option,
)
from thonny.plugins.aia.prompts import answer_style

logger = logging.getLogger(__name__)

KEY_SETTLE_MS = 1000  # after the last change of the key, ask for its models
POLL_MS = 100


# space between a label and its drop-down
COMBO_PAD = 8


def style_names():
    """Answer style id -> name in the current language."""
    return {"socratic": tr("Socratic"), "explanatory": tr("Explanatory"), "direct": tr("Direct")}


# No "ask your teacher" (Jakub 2026-11-12, cortex-zamestnani#117): students get no key from the
# school, the AI assistant needs their own API key or a local model
def _cloud_note(provider):
    return tr(
        "Answers come from %s. You need an API key: create it at the link below."
    ) % first_run.COMPANY[provider]


# Red, below the key, when the provider refused it (Jakub 2026-11-12, cortex-zamestnani#117);
# a provider that cannot be reached says so in the model note instead
REJECTED_COLOR = "#c0392b"


def _rejected_note(provider):
    return tr(
        "%s does not accept this key. Check it, or create a new one at the link below."
    ) % first_run.COMPANY[provider]


def _later_note():
    return tr("No provider is chosen: AIA stays switched on, but it does not answer.")


class AIAPanelConfigPage(ConfigurationPage):
    def __init__(self, master):
        super().__init__(master)
        self._core = AIACore()
        self._core.register_providers()
        wb = get_workbench()

        self._models = first_run._default_models()
        self._key_urls = first_run._key_urls()
        # provider -> chosen model ("" = default), and the models listed for its key
        self._chosen = {
            p: (wb.get_option(model_option(p)) or "").strip() for p in MODEL_CHOICE_PROVIDERS
        }
        self._listed = {}  # provider -> (key, models) of the last successful listing
        self._listing = queue.Queue()  # (provider, key, models or None) from worker threads
        self._listing_key = {}  # provider -> key whose models are being asked for
        self._key_timer = None
        self._poll_timer = None
        self._failed = {}  # provider -> key whose listing failed
        self._rejected = {}  # provider -> key the provider refused
        self._model_ids = []
        self._labels = first_run._ai_labels(self._current_models())

        stored = wb.get_option("aia.provider") or first_run.AI_LATER
        self._ai = stored if stored in self._labels else first_run.AI_LATER
        # A provider set by hand in configuration.ini (e.g. claude_code) is not offered here:
        # it stays until the choice is changed on the page
        self._kept_provider = None if stored in self._labels else stored
        self._ollama = None  # last first_run.ollama_state(), checked when the local model is shown
        self._keys = {}  # provider -> key as edited on this page
        self._link_url = ""

        row = 0
        self._enabled_var = wb.get_variable("aia.enabled")
        ttk.Checkbutton(
            self,
            text=tr("Use AI-based AIA instead of the built-in Assistant"),
            variable=self._enabled_var,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10))
        row += 1

        self._choice_labels = [ttk.Label(self, text=tr("AI assistant:"))]
        self._choice_labels[-1].grid(row=row, column=0, sticky="w", pady=2)
        self._ai_var = tk.StringVar(value=self._labels[self._ai])
        self._ai_combo = ttk.Combobox(
            self,
            textvariable=self._ai_var,
            values=list(self._labels.values()),
            state="readonly",
            exportselection=False,
            width=36,
        )
        self._ai_combo.grid(row=row, column=1, sticky="w", padx=(COMBO_PAD, 0), pady=2)
        self._ai_combo.bind("<<ComboboxSelected>>", self._on_ai_changed)
        row += 1

        self._detail = self._make_detail(ttk.Frame(self))
        self._detail.frame.grid(row=row, column=0, columnspan=2, sticky="nw", pady=(4, 8))
        row += 1

        # --- Answer style ---
        self._choice_labels.append(ttk.Label(self, text=tr("Answer style:")))
        self._choice_labels[-1].grid(row=row, column=0, sticky="w", pady=2)
        self._mentoring_var = wb.get_variable("aia.mentoring_level")
        self._style_names = style_names()
        self._style_display_var = tk.StringVar(
            value=self._style_names.get(answer_style(self._mentoring_var.get()))
        )
        style_combo = ttk.Combobox(
            self,
            textvariable=self._style_display_var,
            values=list(self._style_names.values()),
            state="readonly",
            width=18,
        )
        style_combo.grid(row=row, column=1, sticky="w", padx=(COMBO_PAD, 0), pady=2)
        style_combo.bind("<<ComboboxSelected>>", self._on_style_changed)
        row += 1

        self._legend = ttk.Label(
            self,
            text=tr(
                "Socratic = guides you to the answer, Explanatory = explains and answers, "
                "Direct = short answer only"
            ),
            foreground="#7f8c8d",
        )
        self._legend.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1

        # --- Context section (separated) ---
        ttk.Separator(self, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(5, 5)
        )
        row += 1

        ttk.Label(self, text=tr("Context:"), font="TkDefaultFont 9 bold").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 2)
        )
        row += 1

        ttk.Checkbutton(
            self,
            text=tr("Automatically include current file as context"),
            variable=wb.get_variable("aia.auto_attach_context"),
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        ttk.Checkbutton(
            self,
            text=tr("Also include locally imported files"),
            variable=wb.get_variable("aia.include_local_imports"),
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        self._align_combos()
        self._fix_detail_size()
        self._show(self._ai)

    # --- AI choice ---

    def _make_detail(self, frame):
        d = types.SimpleNamespace(frame=frame)
        frame.rowconfigure(0, weight=1)
        frame.rowconfigure(5, weight=1)
        d.note = ttk.Label(frame, wraplength=460, justify="left")
        d.note.grid(row=1, column=1, sticky="nw")
        d.key_frame = ttk.Frame(frame)
        d.key_frame.grid(row=2, column=1, sticky="nw", pady=(7, 0))
        # the model rows below the key are shown for the local model too, without the key
        d.key_widgets = [ttk.Label(d.key_frame, text=tr("API key:"))]
        self._choice_labels.append(d.key_widgets[-1])
        d.key_widgets[-1].grid(row=1, column=1, sticky="w")
        self._key_var = tk.StringVar()
        d.key_widgets.append(ttk.Entry(d.key_frame, textvariable=self._key_var, show="*", width=36))
        d.key_widgets[-1].grid(row=1, column=2, sticky="w", padx=(COMBO_PAD, 0))
        d.key_error = ttk.Label(d.key_frame, foreground=REJECTED_COLOR)
        d.key_error.grid(row=2, column=1, columnspan=2, sticky="w", pady=(2, 0))
        # Thonny 5 has no secrets store (the API key dialog said so before, keep saying it)
        d.key_widgets.append(
            ttk.Label(
                d.key_frame,
                text=tr("The key is stored in plain text in Thonny's configuration file."),
                foreground="#7f8c8d",
            )
        )
        d.key_widgets[-1].grid(row=3, column=1, columnspan=2, sticky="w", pady=(2, 0))
        self._choice_labels.append(ttk.Label(d.key_frame, text=tr("Model:")))
        self._choice_labels[-1].grid(row=4, column=1, sticky="w", pady=(7, 0))
        self._model_var = tk.StringVar()
        d.model_combo = ttk.Combobox(
            d.key_frame,
            textvariable=self._model_var,
            state="readonly",
            exportselection=False,
            width=34,
        )
        d.model_combo.grid(row=4, column=2, sticky="w", padx=(COMBO_PAD, 0), pady=(7, 0))
        d.model_combo.bind("<<ComboboxSelected>>", self._on_model_changed)
        d.model_note = ttk.Label(d.key_frame, foreground="#7f8c8d")
        d.model_note.grid(row=5, column=1, columnspan=2, sticky="w", pady=(2, 0))
        self._key_var.trace_add("write", self._on_key_changed)
        # width -6 = at least 6 characters: Thonny's UI themes give TButton a fixed width of 6,
        # which cut "Zkontrolovat znovu" to "Zkontrolo" (until beta 6)
        d.check_button = ttk.Button(
            frame, text=tr("Check again"), command=self._on_check_ollama, width=-6
        )
        d.check_button.grid(row=3, column=1, sticky="nw", pady=(7, 0))
        d.link = ui_utils.create_action_label(frame, "", lambda e: webbrowser.open(self._link_url))
        d.link.grid(row=4, column=1, sticky="nw", pady=(3, 0))
        return d

    def _fill(self, ai, ollama, measure=False, local_model=None):
        """Show the detail of one choice (and one Ollama state for the local model).

        ``measure``: the longest variant of the texts, for _fix_detail_size.
        ``local_model``: the local model the note speaks of, else the current one.
        """
        d = self._detail
        link = ""
        if ai in first_run.AI_CLOUD:
            d.note.configure(text=_cloud_note(ai))
            self._show_models(ai, measure)
            for widget in d.key_widgets:
                widget.grid()
            d.key_frame.grid()
            d.check_button.grid_remove()
            link = self._key_urls[ai]
        elif ai == first_run.AI_LOCAL:
            model = local_model or self._current_models()[ai]
            d.note.configure(text=first_run._local_note(ollama, model))
            self._show_models(ai, measure)
            for widget in d.key_widgets:
                widget.grid_remove()
            d.key_frame.grid()
            if ollama == "ready":
                d.check_button.grid_remove()
            else:
                d.check_button.grid()
            link = first_run.OLLAMA_URL if ollama in ("missing", "stopped") else ""
        else:
            d.note.configure(text=_later_note())
            d.key_frame.grid_remove()
            d.check_button.grid_remove()
        d.link.configure(text=link)
        if link:
            d.link.grid()
        else:
            d.link.grid_remove()
        self._link_url = link

    def _align_combos(self):
        """Put every drop-down (and the key) right after the longest of their labels.

        Until beta 6 the drop-downs stood far to the right of their labels (Jakub 2026-10-17,
        cortex-zamestnani#80): the label column had no width of its own and took a share
        of the lines that span both columns (the legend of the answer styles).
        """
        self.update_idletasks()
        width = max(label.winfo_reqwidth() for label in self._choice_labels)
        self.columnconfigure(0, minsize=width)
        self.columnconfigure(1, weight=1)  # the spanning lines widen this column, not the labels'
        # the key and model rows are in the detail frame, whose column 0 starts at the page's
        self._detail.key_frame.columnconfigure(1, minsize=width)
        self._detail.key_frame.columnconfigure(2, weight=1)  # the notes below the key and model

    def _fix_detail_size(self):
        """Size the detail area for its largest variant before the page is shown, so that
        changing the choice moves nothing below it."""
        variants = [(ai, None, None) for ai in self._labels if ai != first_run.AI_LOCAL]
        # the default local model's texts, and those of another model with a long name
        for model in (self._models[first_run.AI_LOCAL], "x" * 40):
            variants += [
                (first_run.AI_LOCAL, s, model) for s in ("missing", "stopped", "no_model", "ready")
            ]
        # the note is as wide as the page's widest line (the legend of the answer styles)
        self.update_idletasks()
        wraplength = max(self._legend.winfo_reqwidth(), self._ai_combo.winfo_reqwidth())
        self._detail.note.configure(wraplength=wraplength)
        self._detail.model_note.configure(wraplength=wraplength)
        self._detail.key_error.configure(wraplength=wraplength)
        width = height = 0
        for ai, ollama, local_model in variants:
            self._fill(ai, ollama, measure=True, local_model=local_model)
            self._detail.frame.update_idletasks()
            width = max(width, self._detail.frame.winfo_reqwidth())
            height = max(height, self._detail.frame.winfo_reqheight())
        self._detail.frame.configure(width=width, height=height)
        self._detail.frame.grid_propagate(False)

    def _show(self, ai):
        if ai == first_run.AI_LOCAL:
            if self._ollama is None:
                self._ollama = first_run.ollama_state(self._current_models()[ai])
            self._ask_models(ai)
        if ai in first_run.AI_CLOUD:
            if ai not in self._keys:
                self._keys[ai] = self._core._get_stored_api_key(ai) or ""
            self._key_var.set(self._keys[ai])
            self._ask_models(ai)  # now, not after KEY_SETTLE_MS
        self._fill(ai, self._ollama)

    def _on_ai_changed(self, event=None):
        if self._ai in first_run.AI_CLOUD:
            self._keys[self._ai] = self._key_var.get()
        self._ai = list(self._labels)[self._ai_combo.current()]
        self._kept_provider = None
        self._show(self._ai)

    def _on_check_ollama(self):
        local = first_run.AI_LOCAL
        self._ollama = first_run.ollama_state(self._current_models()[local])
        # ask for the models again: Ollama may have started or got another model since
        self._listed.pop(local, None)
        self._failed.pop(local, None)
        self._ask_models(local)
        self._fill(local, self._ollama)

    def _on_style_changed(self, event=None):
        shown = self._style_display_var.get()
        for style, name in self._style_names.items():
            if name == shown:
                self._mentoring_var.set(style)

    def apply(self, changed_options=None):
        # 4.x/5.0 call apply() without arguments, newer Thonny passes changed_options
        wb = get_workbench()
        if self._ai in first_run.AI_CLOUD:
            self._keys[self._ai] = self._key_var.get()
        for provider, key in self._keys.items():
            if key.strip() != (self._core._get_stored_api_key(provider) or ""):
                self._core.set_api_key(provider, key)
        if self._kept_provider is None:
            wb.set_option("aia.provider", "" if self._ai == first_run.AI_LATER else self._ai)
        for provider, model in self._chosen.items():
            wb.set_option(model_option(provider), model)
        wb.set_option("aia.model", "")  # a model chosen in earlier versions no longer applies
        return True

    # --- Model (providers with an API key, and the local model) ---

    def _current_models(self):
        """Provider id -> the model it answers with (the chosen one or the default)."""
        models = dict(self._models)
        models.update({p: m for p, m in self._chosen.items() if m})
        return models

    def _model_values(self, provider):
        """Model ids offered: those listed for the key on the page, else the usual ones."""
        default = self._models[provider]
        listed = self._listed.get(provider)
        if listed and listed[0] == self._token(provider):
            models = list(listed[1])
        else:
            registered = self._core.providers.get(provider)
            models = registered.get_available_models() if registered else []
        if default not in models:
            models.insert(0, default)
        chosen = self._chosen.get(provider)
        if chosen and chosen not in models:
            models.append(chosen)  # stays offered until another model is chosen
        return models

    def _model_label(self, provider, model):
        return tr("%s (default)") % model if model == self._models[provider] else model

    def _token(self, provider):
        """What the listing depends on: the key on the page; the local model needs none."""
        return "local" if provider == first_run.AI_LOCAL else self._key_var.get().strip()

    def _model_notes(self, provider):
        if provider == first_run.AI_LOCAL:
            return {
                "asking": tr("Asking Ollama for the models on this computer..."),
                "listed": tr("The models Ollama has on this computer."),
                "failed": tr("Ollama did not answer, so the models on this computer are unknown."),
            }
        # Until beta 8 a refused key and a provider that cannot be reached both said "The
        # provider did not list the models of this key; these are the usual ones" (Jakub
        # 2026-11-12, cortex-zamestnani#117: the student does not know what a provider is, why
        # it should list models and why it matters). A refused key is now red below the key.
        return {
            "no_key": tr("Enter the API key to see the models it can use."),
            "asking": tr("Asking the provider for the models of this key..."),
            "listed": tr("The models this key can use."),
            "failed": tr("Could not connect to the AI provider, so the key cannot be checked now."),
            "rejected": "",
        }

    def _model_state(self, provider):
        key = self._token(provider)
        listed = self._listed.get(provider)
        if not key:
            return "no_key"
        if self._listing_key.get(provider) == key:
            return "asking"
        if listed and listed[0] == key:
            return "listed"
        if self._rejected.get(provider) == key:
            return "rejected"
        if self._failed.get(provider) == key:
            return "failed"
        return None

    def _model_note(self, provider):
        state = self._model_state(provider)
        return self._model_notes(provider)[state] if state else ""

    def _show_models(self, provider, measure=False):
        d = self._detail
        self._model_ids = self._model_values(provider)
        d.model_combo.configure(values=[self._model_label(provider, m) for m in self._model_ids])
        current = self._chosen.get(provider) or self._models[provider]
        self._model_var.set(self._model_label(provider, current))
        if measure:
            note = max(self._model_notes(provider).values(), key=len)
        else:
            note = self._model_note(provider)
        d.model_note.configure(text=note)
        if provider in first_run.AI_CLOUD and (measure or self._model_state(provider) == "rejected"):
            d.key_error.configure(text=_rejected_note(provider))
            d.key_error.grid()
        else:
            d.key_error.configure(text="")
            d.key_error.grid_remove()

    def _on_model_changed(self, event=None):
        model = self._model_ids[self._detail.model_combo.current()]
        self._chosen[self._ai] = "" if model == self._models[self._ai] else model
        # the AI choice names the model: "Anthropic (Claude Opus 5.5)"
        self._labels = first_run._ai_labels(self._current_models())
        self._ai_combo.configure(values=list(self._labels.values()))
        self._ai_var.set(self._labels[self._ai])
        if self._ai == first_run.AI_LOCAL:  # the note says whether this model is downloaded
            self._ollama = first_run.ollama_state(self._current_models()[self._ai])
            self._fill(self._ai, self._ollama)

    def _on_key_changed(self, *_args):
        if self._ai not in first_run.AI_CLOUD:
            return
        if self._key_timer is not None:
            self.after_cancel(self._key_timer)
        self._key_timer = self.after(KEY_SETTLE_MS, self._on_key_settled)
        self._show_models(self._ai)

    def _on_key_settled(self):
        self._key_timer = None
        if self._ai in first_run.AI_CLOUD:
            self._ask_models(self._ai)
            self._show_models(self._ai)

    def _ask_models(self, provider):
        """Ask the provider which models the key on the page can use (Ollama: which models
        it has), in a worker thread."""
        key = self._token(provider)
        registered = self._core.providers.get(provider)
        listed = self._listed.get(provider)
        if (
            not key
            or registered is None
            or (listed and listed[0] == key)
            or key
            in (
                self._listing_key.get(provider),
                self._failed.get(provider),
                self._rejected.get(provider),
            )
        ):
            return
        asking = copy.copy(registered.frozen())  # settings read here, on the UI thread
        if provider in first_run.AI_CLOUD:
            asking._get_api_key = lambda: key  # the key on the page, maybe not saved yet
        self._listing_key[provider] = key

        def work():
            rejected = False
            try:
                models = asking.list_models()
            except Exception as e:
                logger.info("AIA: could not list the models of %s", provider, exc_info=True)
                models = None
                rejected = provider in first_run.AI_CLOUD and is_rejected_key(e)
            self._listing.put((provider, key, models, rejected))

        threading.Thread(target=work, daemon=True).start()
        if self._poll_timer is None:
            self._poll_timer = self.after(POLL_MS, self._poll_listing)

    def _poll_listing(self):
        self._poll_timer = None
        try:
            if not self.winfo_exists():
                return  # the Options dialog was closed
        except tk.TclError:
            return
        try:
            while True:
                provider, key, models, rejected = self._listing.get_nowait()
                if self._listing_key.get(provider) == key:
                    del self._listing_key[provider]
                if models:
                    self._listed[provider] = (key, models)
                elif rejected:
                    self._rejected[provider] = key
                else:
                    self._failed[provider] = key
                if provider == self._ai:
                    self._show_models(provider)
        except queue.Empty:
            pass
        if self._listing_key:
            self._poll_timer = self.after(POLL_MS, self._poll_listing)
