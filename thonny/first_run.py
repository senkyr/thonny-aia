import json
import os.path
import shutil
import sys
import tkinter as tk
import types
import urllib.request
import webbrowser
from tkinter import ttk

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

OLLAMA_URL = "https://ollama.com/download"
OLLAMA_API = "http://127.0.0.1:11434"
OLLAMA_TIMEOUT = 1.5  # seconds, the check runs on the UI thread


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


def _find_ollama():
    """Path of the installed ollama executable, or None. An app started from the Finder
    usually lacks /usr/local/bin and /opt/homebrew/bin in PATH, so look there explicitly."""
    paths = [
        shutil.which("ollama"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
    ]
    if sys.platform == "darwin":
        paths += [
            "/usr/local/bin/ollama",
            "/opt/homebrew/bin/ollama",
            "/Applications/Ollama.app/Contents/Resources/ollama",
            os.path.expanduser("~/Applications/Ollama.app/Contents/Resources/ollama"),
        ]
    return next((p for p in paths if p and os.path.isfile(p)), None)


def ollama_state(model):
    """'missing' (not installed), 'stopped' (installed, server not answering),
    'no_model' (running, model not downloaded) or 'ready'."""
    exe = _find_ollama()
    # no proxy: a school proxy from the Windows registry answers 127.0.0.1 with 503
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(OLLAMA_API + "/api/tags", timeout=OLLAMA_TIMEOUT) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return "stopped" if exe else "missing"
    names = set()
    for m in tags.get("models", []):
        names.update({m.get("name"), m.get("model")})
    return "ready" if model in names else "no_model"


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
    labels[AI_LOCAL] = tr("Local model (Ollama, %s)") % models[AI_LOCAL]
    return labels


def _cloud_note(provider):
    return tr(
        "Answers come from %s. You need an API key: create it at the link below, or get one "
        "from your teacher. You can also enter it later in Tools > Options > AI Assistant."
    ) % COMPANY[provider]


def _local_note(state, model):
    # Kept about as long as the cloud note (Jakub 2026-10-10): the detail area is sized for the
    # longest variant, so a long local note left the usual dialog half empty.
    # The sizes and the speed are those of the default model; another model chosen in Tools >
    # Options (cortex-zamestnani#80) gets the texts without them.
    default = model == _default_models()[AI_LOCAL]
    if state == "missing":
        if default:
            text = tr(
                "Ollama is not installed. Install it from the link below, then download the "
                "model: 'ollama pull %s' (13 GB)."
            ) % model
        else:
            text = tr(
                "Ollama is not installed. Install it from the link below, then download the "
                "model: 'ollama pull %s'."
            ) % model
    elif state == "stopped":
        text = tr("Ollama is installed but not running. Start it.")
    elif state == "no_model":
        if default:
            text = tr(
                "Ollama is running, but the model is missing. Download it: 'ollama pull %s' (13 GB)."
            ) % model
        else:
            text = tr(
                "Ollama is running, but the model is missing. Download it: 'ollama pull %s'."
            ) % model
    else:
        text = tr("Ollama is running and the model %s is ready.") % model
    if default:
        tail = tr(
            "It runs offline, needs about 14 GB of free RAM and, without a strong graphics card, "
            "answers in 20-70 s."
        )
    else:
        tail = tr(
            "It runs offline; how much memory it needs and how fast it answers depends on the "
            "model and the computer."
        )
    return text + " " + tail


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
    def __init__(self, configuration_manager):
        super().__init__(className="Thonny")
        ttk.Style().theme_use(ui_utils.get_default_basic_theme())

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.ok = False

        self.conf = configuration_manager
        self.models = _default_models()
        self.key_urls = _key_urls()
        # last ollama_state(); checked right away only if the local model is preselected
        self.ollama = ollama_state(self.models[AI_LOCAL]) if AI_DEFAULT == AI_LOCAL else None

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

        languages.set_language(DEFAULT_LANGUAGE_CODE)
        self.language_variable = ui_utils.create_string_var(
            languages.LANGUAGES_DICT[DEFAULT_LANGUAGE_CODE], self.on_change_language
        )
        self.add_combo(
            1,
            lambda: tr("Language:"),
            self.language_variable,
            list(languages.LANGUAGES_DICT.values()),
        )

        self.mode = STD_MODE
        self.mode_variable = tk.StringVar()
        self.mode_combo = self.add_combo(
            2, lambda: tr("Initial settings:"), self.mode_variable, [], self._update_mode_values
        )
        self.mode_combo.bind("<<ComboboxSelected>>", self.on_change_mode, True)

        self.ai = AI_DEFAULT
        self.ai_variable = tk.StringVar()
        self.ai_combo = self.add_combo(
            3, lambda: tr("AI assistant:"), self.ai_variable, [], self._update_ai_values
        )
        self.ai_combo.bind("<<ComboboxSelected>>", self.on_change_ai, True)

        # Detail of the AI choice (note, key or "Check again", link) in a frame of fixed size.
        # The same widgets once more in a frame that is never shown: the size of every variant
        # is measured there, so measuring redraws nothing on screen.
        self.key_variable = tk.StringVar(value="")
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
        self.center()

    def on_change_language(self):
        languages.set_language(
            languages.get_language_code_by_name(self.language_variable.get())
        )
        self._retranslate()

    def on_change_mode(self, event=None):
        self.mode = list(_mode_labels())[self.mode_combo.current()]

    def on_change_ai(self, event=None):
        new = list(_ai_labels(self.models))[self.ai_combo.current()]
        if new != self.ai:
            self.key_variable.set("")  # a key belongs to one provider
        self.ai = new
        if self.ai == AI_LOCAL and self.ollama is None:
            self.ollama = ollama_state(self.models[AI_LOCAL])
        self._show_detail(self.detail, self.ai, self.ollama)

    def on_check_ollama(self):
        self.ollama = ollama_state(self.models[AI_LOCAL])
        self._show_detail(self.detail, self.ai, self.ollama)

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
        self._show_detail(self.detail, self.ai, self.ollama)

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
        d.check_button = ttk.Button(frame, command=self.on_check_ollama)
        d.check_button.grid(row=2, column=1, sticky="nw", pady=(7, 0))
        d.link = ui_utils.create_action_label(frame, "", lambda e: webbrowser.open(self._link_url))
        d.link.grid(row=3, column=1, sticky="nw", pady=(3, 0))
        return d

    def _show_detail(self, d, ai, ollama):
        """Fill a detail area for one AI choice (and one Ollama state for the local model)."""
        d.key_label.configure(text=tr("API key:"))
        d.check_button.configure(text=tr("Check again"))
        if ai in AI_CLOUD:
            d.note.configure(text=_cloud_note(ai))
            d.key_frame.grid()
            d.check_button.grid_remove()
            link = self.key_urls[ai]
        elif ai == AI_LOCAL:
            d.note.configure(text=_local_note(ollama, self.models[AI_LOCAL]))
            d.key_frame.grid_remove()
            if ollama == "ready":
                d.check_button.grid_remove()
            else:
                d.check_button.grid()
            link = OLLAMA_URL if ollama in ("missing", "stopped") else ""
        else:
            d.note.configure(text=_later_note())
            d.key_frame.grid_remove()
            d.check_button.grid_remove()
            link = ""
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
        variants = [(p, None) for p in AI_CLOUD] + [(AI_LATER, None)]
        variants += [(AI_LOCAL, s) for s in ("missing", "stopped", "no_model", "ready")]
        # the note uses the full width of the choices above it (labels + combos), not a fixed
        # 400 px: fewer lines, so the area reserved for the longest variant is lower
        self.update_idletasks()
        wrap = max(l.winfo_reqwidth() for l in self._combo_labels) + 10 + self.ai_combo.winfo_reqwidth()
        for d in (self.detail, self.probe):
            d.note.configure(wraplength=wrap)
        self.data_label.configure(wraplength=wrap)
        width = height = 0
        for ai, ollama in variants:
            self._show_detail(self.probe, ai, ollama)
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

        self.conf.save()

        self.ok = True
        self.destroy()
