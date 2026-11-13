import os
import queue
import threading
import tkinter as tk
import types
import webbrowser
from tkinter import ttk

import thonny
from thonny import APP_NAME, is_portable, languages, ui_utils
from thonny.languages import tr

# Thonny AIA starts in Czech; the other languages stay one click away
DEFAULT_LANGUAGE_CODE = "cs_CZ"

STD_MODE = "standard"
RPI_MODE = "rpi"

# AI Assistant (thonny.plugins.aia): what the welcome dialog offers, as provider ids.
# "later" is preselected (Jakub 2026-10-13, cortex-zamestnani#76; until beta 4 the local model
# was): the start is simpler and a new user does not end up with gpt-oss:20b, weak and slow,
# loading 13 GB at the first start. "later" keeps AIA on and saves no provider: the choice is
# made later in Tools > Options.
AI_CLOUD = ("anthropic", "openai", "gemini")
AI_LOCAL = "ollama"
AI_LATER = "later"
AI_DEFAULT = AI_LATER
COMPANY = {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Google"}

LOCAL_POLL_MS = 100  # the local model is checked in a worker thread (local_ui.check_model)


def _default_models():
    """Provider id -> default model, taken from the AIA providers (one place to change it)."""
    from thonny.plugins.aia.providers.anthropic_provider import AnthropicProvider
    from thonny.plugins.aia.providers.gemini_provider import GeminiProvider
    from thonny.plugins.aia.providers.ollama_provider import OllamaProvider
    from thonny.plugins.aia.providers.openai_provider import OpenAIProvider

    none = lambda: None
    return {
        "anthropic": AnthropicProvider(get_api_key=none).get_default_model(),
        "openai": OpenAIProvider(get_api_key=none).get_default_model(),
        "gemini": GeminiProvider(get_api_key=none).get_default_model(),
        "ollama": OllamaProvider().get_default_model(),
    }


def _key_urls():
    from thonny.plugins.aia._common import API_KEY_URLS

    return API_KEY_URLS


def ai_works(conf) -> bool:
    """Whether the stored configuration has a working AI (no network: the key is not tried).

    The welcome dialog comes once more after an update for those without it (Jakub
    2026-11-12, cortex-zamestnani#118): no provider (beta 8's "I will set it up later"), a
    provider without its key, or the local model that is not downloaded. AIA switched off
    counts as working: the user chose the built-in Assistant.
    """
    if str(conf.get_option("aia.enabled", True)) in ("False", "false", "0"):
        return True
    provider = conf.get_option("aia.provider", "") or ""
    provider = str(provider).strip()
    if provider in AI_CLOUD:
        from thonny.plugins.aia._common import API_KEY_ENV_VARS

        key = str(conf.get_option("aia.%s_api_key" % provider, "") or "").strip()
        return bool(key) or any(os.environ.get(v, "").strip() for v in API_KEY_ENV_VARS[provider])
    if provider == AI_LOCAL:
        if str(conf.get_option("aia.ollama_host", "") or "").strip():
            return True  # an Ollama set by hand: not this dialog's business
        from thonny.plugins.aia import local_models as lm

        model = str(conf.get_option("aia.ollama_model", "") or "").strip() or _default_models()[AI_LOCAL]
        return lm.has_own_model(model) or lm.has_model(lm.SYSTEM_HOST, model, timeout=1.5)
    return provider not in ("", AI_LATER)  # e.g. claude_code set by hand


def needs_welcome_again(conf, version: str) -> bool:
    """After an update to another version, once, for those without a working AI."""
    shown = str(conf.get_option("aia.welcome_version", "") or "")
    return shown != version and not ai_works(conf)


def _mode_labels():
    """Choice -> label in the current language (the combos keep the choice, not the text)"""
    return {STD_MODE: tr("Standard"), RPI_MODE: tr("Raspberry Pi (simple)")}


# Readable names of the default models and of the usual others (a model chosen in Tools >
# Options shows in its provider's label there); an ID not listed here is shown as it is
MODEL_NAMES = {
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-opus-5-5": "Claude Opus 5.5",
    "gpt-6-luna": "GPT-6 Luna",
    "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-astra": "GPT-6 Astra",
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
    "gemini-3.8-flash": "Gemini 3.8 Flash",
}


def _ai_labels(models):
    # Order of the choice: "later" first, providers by company name, local model last (Jakub
    # 2026-10-13, cortex-zamestnani#76: the two swapped places; until beta 4 it was local first)
    labels = {AI_LATER: tr("I will set it up later")}
    for p in sorted(AI_CLOUD, key=lambda p: COMPANY[p].lower()):
        labels[p] = "%s (%s)" % (COMPANY[p], MODEL_NAMES.get(models[p], models[p]))
    # no "Ollama" since the application prepares it (cortex-zamestnani#120): the student
    # installs nothing and does not need the name
    labels[AI_LOCAL] = tr("Local model (%s)") % models[AI_LOCAL]
    return labels


def _cloud_note(provider):
    # No "get one from your teacher" (Jakub 2026-11-12, cortex-zamestnani#117): the school gives
    # students no key
    return tr(
        "Answers come from %s. You need an API key: create it at the link below. You can also "
        "enter it later in Tools > Options > AI Assistant."
    ) % COMPANY[provider]


def data_notice():
    """What AIA sends and where, in the welcome dialog and in About (Jakub 2026-10-15,
    cortex-zamestnani#78). Checked against the code: the messages, the editor's file (option
    aia.auto_attach_context, on by default) and the files it imports from its folder (option
    aia.include_local_imports, off by default), the Shell output after an error and pip's log
    after a failed installation go to the chosen provider; Ollama runs on the computer."""
    return tr(
        "AIA sends your messages, the code from the editor (and, if switched on in the options, "
        "the files it imports) and error output to the AI provider you choose. The local model "
        "sends nothing outside your computer."
    )


def _later_note():
    return tr(
        "AIA stays switched on, but it will answer only after you choose a provider in "
        "Tools > Options > AI Assistant."
    )


class FirstRunWindow(tk.Tk):
    def __init__(self, configuration_manager, upgrade=False):
        super().__init__(className="Thonny")
        ttk.Style().theme_use(ui_utils.get_default_basic_theme())

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.ok = False

        self.conf = configuration_manager
        # After an update the dialog comes once more for those without a working AI, filled
        # in with what they have (cortex-zamestnani#118); closing it does not end Thonny then
        self.upgrade = upgrade
        self.models = _default_models()
        stored_model = str(self._stored("aia.ollama_model") or "").strip()
        if upgrade and stored_model:
            self.models[AI_LOCAL] = stored_model
        self.key_urls = _key_urls()
        # where the local model is and what preparing it takes (local_ui.check_model), asked
        # in a worker thread when the local model is shown
        self.local_check = None
        self._local_asking = False
        self._local_results = queue.Queue()

        self.main_frame = ttk.Frame(self)
        self.main_frame.grid(row=1, column=1, sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        # No row stretches: the choices stay where they are whatever the AI note says
        # (the detail area below them has a fixed size, see _fix_detail_size)

        logo_file = os.path.join(os.path.dirname(__file__), "res", "thonny.png")
        self.logo = tk.PhotoImage(file=logo_file)
        self.iconphoto(True, self.logo)  # not the Tk feather in the title bar

        logo_label = ttk.Label(self.main_frame, image=self.logo)
        logo_label.grid(row=1, rowspan=4, column=1, sticky="n", pady=(7, 0))  # top level with the first choice

        self.padx = 50
        self.pady = 50
        self._translatable = []  # (widget, function giving its current text)
        self._combo_labels = []  # labels of the choices, their width sets the note width

        language = DEFAULT_LANGUAGE_CODE
        if upgrade and self._stored("general.language") in languages.LANGUAGES_DICT:
            language = self._stored("general.language")
        languages.set_language(language)
        self._set_dialog_language(language)
        self.language_variable = ui_utils.create_string_var(
            languages.LANGUAGES_DICT[language], self.on_change_language
        )
        self.add_combo(
            1,
            lambda: tr("Language:"),
            self.language_variable,
            list(languages.LANGUAGES_DICT.values()),
        )

        self.mode = RPI_MODE if upgrade and self._stored("general.ui_mode") == "simple" else STD_MODE
        self.mode_variable = tk.StringVar()
        self.mode_combo = self.add_combo(
            2, lambda: tr("Initial settings:"), self.mode_variable, [], self._update_mode_values
        )
        self.mode_combo.bind("<<ComboboxSelected>>", self.on_change_mode, True)

        self.ai = AI_DEFAULT
        stored_ai = str(self._stored("aia.provider") or "").strip()
        if upgrade and stored_ai in _ai_labels(self.models):
            self.ai = stored_ai
        self.ai_variable = tk.StringVar()
        self.ai_combo = self.add_combo(
            3, lambda: tr("AI assistant:"), self.ai_variable, [], self._update_ai_values
        )
        self.ai_combo.bind("<<ComboboxSelected>>", self.on_change_ai, True)

        # Detail of the AI choice (note, key or "Check again", link) in a frame of fixed size.
        # The same widgets once more in a frame that is never shown: the size of every variant
        # is measured there, so measuring redraws nothing on screen.
        stored_key = self._stored("aia.%s_api_key" % self.ai) if self.ai in AI_CLOUD else ""
        self.key_variable = tk.StringVar(value=str(stored_key or "") if upgrade else "")
        self._link_url = ""
        self.detail = self._make_detail(ttk.Frame(self.main_frame))
        self.detail.frame.grid(
            row=4, column=2, columnspan=2, sticky="nw", padx=(0, self.padx), pady=(7, 0)
        )
        self.probe = self._make_detail(ttk.Frame(self.main_frame))  # never gridded
        self._sized_for = None  # language the detail size was measured for

        self.data_label = ttk.Label(self.main_frame, justify="left")
        self.data_label.grid(
            row=5, column=2, columnspan=2, sticky="nw", padx=(0, self.padx), pady=(12, 0)
        )
        self._translatable.append((self.data_label, data_notice))

        self.ok_button = ttk.Button(self.main_frame, command=self.on_ok)
        self.ok_button.grid(
            row=6, column=3, padx=(0, self.padx), pady=(12, 20), sticky="se"
        )
        self._translatable.append((self.ok_button, lambda: tr("Let's go!")))

        self._retranslate()
        if self.ai == AI_LOCAL:
            self._ask_local()
        self.center()

    def _stored(self, name):
        """A stored option (after an update), None when there is none."""
        try:
            return self.conf.get_option(name, None)
        except Exception:
            return None

    def _set_dialog_language(self, code):
        from thonny.plugins.aia import local_ui

        local_ui.dialog_language = code  # sizes with a decimal comma in Czech

    def on_change_language(self):
        code = languages.get_language_code_by_name(self.language_variable.get())
        languages.set_language(code)
        self._set_dialog_language(code)
        self._retranslate()

    def on_change_mode(self, event=None):
        self.mode = list(_mode_labels())[self.mode_combo.current()]

    def on_change_ai(self, event=None):
        new = list(_ai_labels(self.models))[self.ai_combo.current()]
        if new != self.ai:
            self.key_variable.set("")  # a key belongs to one provider
        self.ai = new
        if self.ai == AI_LOCAL:
            self._ask_local()
        self._show_detail(self.detail, self.ai)

    # --- the local model that the application prepares (cortex-zamestnani#120) ---

    def _ask_local(self):
        """Where the local model is and what preparing it takes, in a worker thread."""
        if self.local_check is not None or self._local_asking:
            return
        self._local_asking = True
        model = self.models[AI_LOCAL]

        def work():
            from thonny.plugins.aia import local_ui

            try:
                result = local_ui.check_model(model)
            except Exception:
                result = {"model": model, "system": False, "own": False, "info": {"model": model}}
            self._local_results.put(result)

        threading.Thread(target=work, daemon=True).start()
        self.after(LOCAL_POLL_MS, self._poll_local)

    def _poll_local(self):
        try:
            self.local_check = self._local_results.get_nowait()
        except queue.Empty:
            try:
                self.after(LOCAL_POLL_MS, self._poll_local)
            except tk.TclError:
                pass  # the dialog was closed
            return
        self._local_asking = False
        self._show_detail(self.detail, self.ai)

    def _local_view(self):
        from thonny.plugins.aia import local_ui

        return local_ui.local_state(self.local_check, {}), self.local_check

    def _update_mode_values(self, combo):
        labels = _mode_labels()
        combo.configure(values=list(labels.values()))
        self.mode_variable.set(labels[self.mode])

    def _update_ai_values(self, combo):
        labels = _ai_labels(self.models)
        combo.configure(values=list(labels.values()))
        self.ai_variable.set(labels[self.ai])

    def _retranslate(self):
        self.title(tr("Welcome to %s!") % APP_NAME + ("   [portable]" if is_portable() else ""))
        for widget, text in self._translatable:
            if isinstance(widget, ttk.Combobox):
                text(widget)
            else:
                widget.configure(text=text())

        language = languages.get_language_code_by_name(self.language_variable.get())
        if language != self._sized_for:
            self._fix_detail_size()
            self._sized_for = language
        self._show_detail(self.detail, self.ai)

    def _make_detail(self, frame):
        d = types.SimpleNamespace(frame=frame)
        # Empty rows 0 and 4 take the spare height of the fixed-size frame equally, so a shorter
        # variant sits in the middle between the choices and the button (Jakub 2026-10-10)
        frame.rowconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)
        d.note = ttk.Label(frame, wraplength=400, justify="left")
        d.note.grid(row=1, column=1, sticky="nw")
        d.key_frame = ttk.Frame(frame)
        d.key_frame.grid(row=2, column=1, sticky="nw", pady=(7, 0))
        d.key_label = ttk.Label(d.key_frame)
        d.key_label.grid(row=1, column=1, sticky="w")
        entry = ttk.Entry(d.key_frame, textvariable=self.key_variable, show="*", width=36)
        entry.grid(row=1, column=2, sticky="w", padx=(10, 0))
        d.link = ui_utils.create_action_label(frame, "", lambda e: webbrowser.open(self._link_url))
        d.link.grid(row=3, column=1, sticky="nw", pady=(3, 0))
        return d

    def _show_detail(self, d, ai, local=None):
        """Fill a detail area for one AI choice; ``local``: (state, check) of the local model
        to measure, else the one asked for."""
        from thonny.plugins.aia import local_ui

        d.key_label.configure(text=tr("API key:"))
        link = ""
        if ai in AI_CLOUD:
            d.note.configure(text=_cloud_note(ai))
            d.key_frame.grid()
            link = self.key_urls[ai]
        elif ai == AI_LOCAL:
            # The size of the download and the free space; OK is the consent (#118). No
            # "ollama pull" and no link to ollama.com: the application prepares it.
            state, check = local or self._local_view()
            model = self.models[AI_LOCAL]
            d.note.configure(
                text=local_ui.local_note(model, _default_models()[AI_LOCAL], state, check, {})
            )
            d.key_frame.grid_remove()
        else:
            d.note.configure(text=_later_note())
            d.key_frame.grid_remove()
        if d is self.detail:
            self._update_ok()
        d.link.configure(text=link)
        if link:
            d.link.grid()
        else:
            d.link.grid_remove()
        if d is self.detail:
            self._link_url = link

    def _fix_detail_size(self):
        """Size the detail area for its largest variant in the current language, measured on
        the hidden probe, so that switching the AI choice moves and redraws nothing."""
        from thonny.plugins.aia import local_ui

        variants = [(p, None) for p in AI_CLOUD] + [(AI_LATER, None)]
        model = self.models[AI_LOCAL]
        variants += [
            (AI_LOCAL, (state, check))
            for state, check, status in local_ui.measure_variants(model, _default_models()[AI_LOCAL])
            if not status  # the dialog shows no download
        ]
        # the note uses the full width of the choices above it (labels + combos), not a fixed
        # 400 px: fewer lines, so the area reserved for the longest variant is lower
        self.update_idletasks()
        wrap = max(l.winfo_reqwidth() for l in self._combo_labels) + 10 + self.ai_combo.winfo_reqwidth()
        for d in (self.detail, self.probe):
            d.note.configure(wraplength=wrap)
        self.data_label.configure(wraplength=wrap)
        width = height = 0
        for ai, local in variants:
            self._show_detail(self.probe, ai, local)
            self.probe.frame.update_idletasks()
            width = max(width, self.probe.frame.winfo_reqwidth())
            height = max(height, self.probe.frame.winfo_reqheight())
        self.detail.frame.configure(width=width, height=height)
        self.detail.frame.grid_propagate(False)

    def add_combo(self, row, label_text, variable, values, update_values=None):
        pady = 7
        label = ttk.Label(self.main_frame)
        label.grid(row=row, column=2, sticky="sw", pady=(pady, 0))
        self._translatable.append((label, label_text))
        self._combo_labels.append(label)
        assert isinstance(variable, tk.Variable)
        combobox = ttk.Combobox(
            self.main_frame,
            exportselection=False,
            textvariable=variable,
            state="readonly",
            height=15,
            width=36,
            values=values,
        )
        combobox.grid(row=row, column=3, padx=(10, self.padx), sticky="sw", pady=(pady, 0))
        if update_values is not None:
            self._translatable.append((combobox, update_values))
        return combobox

    def center(self):
        width = max(self.winfo_reqwidth(), 640)
        height = max(self.winfo_reqheight(), 300)

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        if screen_width > screen_height * 2:
            # probably dual monitors
            screen_width //= 2

        left = max(int(screen_width / 2 - width / 2), 0)
        top = max(int(screen_height / 2 - height / 2), 0)

        # Positions the window in the center of the page.
        self.geometry("+{}+{}".format(left, top))

    def on_ok(self):
        if self.mode == RPI_MODE:
            self.conf.set_option("debugger.preferred_debugger", "faster")
            self.conf.set_option("view.ui_theme", "Raspberry Pi")
            self.conf.set_option("general.ui_mode", "simple")

        self.conf.set_option(
            "general.language", languages.get_language_code_by_name(self.language_variable.get())
        )

        self.conf.set_option("aia.enabled", True)
        if self.ai != AI_LATER:  # "later" saves no provider: the AIA panel asks for the choice
            self.conf.set_option("aia.provider", self.ai)
            key = self.key_variable.get().strip()
            if self.ai in AI_CLOUD and key:
                # Thonny 5 has no secrets store: the AIA panel reads aia.<provider>_api_key
                self.conf.set_option("aia.%s_api_key" % self.ai, key)
        elif self.upgrade:
            self.conf.set_option("aia.provider", "")  # e.g. a provider whose key is missing
        if self.ai == AI_LOCAL:
            from thonny.plugins.aia import local_models, local_ui

            state, _check = self._local_view()
            if state != local_ui.READY_STATE:
                # OK with the local model = consent to download it, its note said the size
                # (cortex-zamestnani#118); the application prepares it once Thonny runs
                local_models.give_consent(self.models[AI_LOCAL])

        self.conf.set_option("aia.welcome_version", thonny.get_version())
        self.conf.save()

        self.ok = True
        self._finish()

    def on_close(self):
        """Closing the window: a fresh configuration ends Thonny (as upstream); after an
        update Thonny starts and the dialog does not come again."""
        if self.upgrade:
            self.conf.set_option("aia.welcome_version", thonny.get_version())
            self.conf.save()
        self._finish()

    def _finish(self):
        from thonny.plugins.aia import local_ui

        local_ui.dialog_language = None  # the workbench's option rules from now on
        self.destroy()

    def _update_ok(self):
        """OK stays off while the local model cannot be prepared here (too little memory or
        disk space): its note says why and what to do."""
        from thonny.plugins.aia import local_ui

        blocked = False
        if self.ai == AI_LOCAL:
            state, check = self._local_view()
            blocked = state == "consent" and not local_ui.can_prepare((check or {}).get("info") or {})
        self.ok_button.configure(state="disabled" if blocked else "normal")
