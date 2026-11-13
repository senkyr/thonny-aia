# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""Shared logic used by both integration layers (_panel and _chat).

Threading rule: everything that touches Tk or the workbench (options, editor
content, variables) must run on the UI thread. Code in this module is either
pure or explicitly documented as "UI thread only".
"""

import ast
import importlib.util
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from thonny import get_workbench
from thonny.languages import LANGUAGES_DICT, tr

from thonny.plugins.aia.providers.base import LLMProvider

logger = logging.getLogger(__name__)


def interface_language(code: Optional[str]) -> str:
    """Name of Thonny's interface language for the prompt ("Čeština"), without "[BETA]" tags."""
    name = LANGUAGES_DICT.get(code or "", "English")
    return re.sub(r"\s*\[[^\]]*\]\s*", " ", name).strip() or "English"


# Providers that require an API key (Ollama is local, no key needed)
PROVIDERS_NEEDING_KEY = {"openai", "anthropic", "gemini"}

# With an API key the model can be chosen in Tools > Options (Jakub 2026-10-13,
# cortex-zamestnani#75; until beta 3 each provider answered with its default model), the local
# model among the models Ollama has (Jakub 2026-10-16, cortex-zamestnani#80; until beta 6 it
# was always gpt-oss:20b). Stored per provider, "" = the provider's default, so the first
# switch to the local model gives gpt-oss:20b.
MODEL_CHOICE_PROVIDERS = ("anthropic", "gemini", "openai", "ollama")


def model_option(provider_id: str) -> str:
    return "aia.%s_model" % provider_id

# Display name -> internal ID, alphabetical order
PROVIDER_NAMES = {
    "Anthropic": "anthropic",
    "Claude Code (demo)": "claude_code",
    "Gemini": "gemini",
    "Ollama": "ollama",
    "OpenAI": "openai",
}

# Reverse: internal ID -> display name
PROVIDER_DISPLAY = {v: k for k, v in PROVIDER_NAMES.items()}


def provider_display_name(provider_id: str) -> str:
    """Human name of a provider ("openai" -> "OpenAI"); unknown ids come back unchanged."""
    return PROVIDER_DISPLAY.get(provider_id, provider_id)

# Python package (import name) each provider needs, and the pip extra that installs it
PROVIDER_PACKAGES = {
    "anthropic": "anthropic",
    "gemini": "google.genai",
    "ollama": "ollama",
    "openai": "openai",
}

# Environment variables used as a fallback when no key is stored in Thonny.
# Lets a school set keys centrally instead of storing them in each user's config.
API_KEY_ENV_VARS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
}

API_KEY_URLS = {
    "anthropic": "https://platform.claude.com/settings/keys",  # console.anthropic.com redirects here
    "gemini": "https://aistudio.google.com/apikey",
    "openai": "https://platform.openai.com/api-keys",
}

# Limits that keep requests (and student bills) bounded
MAX_HISTORY_MESSAGES = 20
MAX_IMPORTED_FILES = 10
MAX_IMPORTED_FILE_CHARS = 50_000

# Seconds to wait for a Tk-thread callback from a worker thread
UI_CALL_TIMEOUT = 5.0


def package_installed(import_name: str) -> bool:
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ValueError):
        # find_spec("google.genai") raises when the parent package is missing
        return False


def install_hint(provider_id: str) -> str:
    # Thonny AIA bundles every provider's package, so this means a broken installation
    return tr(
        "The '%s' provider needs the Python package '%s', which is missing. "
        "Reinstall Thonny AIA."
    ) % (provider_display_name(provider_id), PROVIDER_PACKAGES.get(provider_id, provider_id))


def find_local_imports(file_path: str, source_code: str) -> Optional[Dict[str, str]]:
    """Parse source for import statements and read locally imported .py files.

    Returns {filename: source_code} for modules found next to ``file_path``,
    or None if there are none. Size-limited so a big helper module can't blow up
    the request.
    """
    if not file_path or not os.path.isfile(file_path):
        return None

    dir_path = os.path.dirname(os.path.abspath(file_path))
    own_path = os.path.normcase(os.path.abspath(file_path))

    try:
        tree = ast.parse(source_code)
    except (SyntaxError, ValueError):
        return None

    module_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_names.append(node.module)

    result: Dict[str, str] = {}
    for module_name in dict.fromkeys(module_names):  # dedupe, keep order
        candidate = os.path.join(dir_path, *module_name.split(".")) + ".py"
        if os.path.normcase(candidate) == own_path or not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                content = f.read(MAX_IMPORTED_FILE_CHARS + 1)
        except (OSError, UnicodeDecodeError):
            logger.debug("Could not read imported file %s", candidate, exc_info=True)
            continue
        if len(content) > MAX_IMPORTED_FILE_CHARS:
            content = content[:MAX_IMPORTED_FILE_CHARS] + "\n# ... (truncated)"
        result[os.path.relpath(candidate, dir_path)] = content
        if len(result) >= MAX_IMPORTED_FILES:
            break

    return result or None


def snapshot_editor_context(wb) -> Dict[str, Optional[object]]:
    """Collect the current editor content. UI thread only.

    Works with both editor APIs: ``get_target_path()`` (Thonny master) and
    ``get_filename()`` (Thonny 4.x / 5.0).
    """
    result = {"code": None, "file_name": None, "imported_files": None}
    if not wb.get_option("aia.auto_attach_context"):
        return result

    try:
        editor = wb.get_editor_notebook().get_current_editor()
        if editor is None:
            return result
        code = editor.get_content()
        if hasattr(editor, "get_target_path"):
            path = editor.get_target_path()
        else:
            path = editor.get_filename()
    except Exception:
        logger.debug("Could not get editor content for context", exc_info=True)
        return result

    result["code"] = code
    result["file_name"] = os.path.basename(path) if path else "unnamed file"

    if wb.get_option("aia.include_local_imports") and path and code:
        result["imported_files"] = find_local_imports(path, code)

    return result


# Enough for the output before the error and a long traceback, cheap for every provider
SHELL_OUTPUT_LIMIT = 4000


def last_run_shell_output(wb) -> str:
    """What the Shell shows for the last run: program output and traceback. UI thread only."""
    try:
        text = wb.get_view("ShellView").text
        output = text.get("command_io_start", "output_end").strip()
    except Exception:
        logger.debug("Could not read the Shell output", exc_info=True)
        return ""
    if len(output) > SHELL_OUTPUT_LIMIT:
        output = "...\n" + output[-SHELL_OUTPUT_LIMIT:]
    return output


def error_report(error_info: Dict, shell_output: str = "") -> str:
    """The first message after a runtime error, sent to the model but not shown in the panel.

    The student wrote nothing, so AIA opens the conversation itself, reacting to what the
    student sees in the Shell (Jakub 2026-10-10: instead of the student "reporting" the error,
    the assistant "reacts" to what is happening). In English: the system prompt says in which
    language to answer.
    """
    type_name = error_info.get("type_name", "Error")
    message = error_info.get("message", "")
    lineno = error_info.get("lineno")
    error = f"{type_name}: {message}" + (f" (line {lineno})" if lineno else "")
    shown = shell_output or (error_info.get("traceback_text") or "").strip() or error
    return (
        "[Automatic notice from Thonny - the student did not type this.] The student ran their "
        f"program and it stopped with {error}. This is what the student sees in Thonny's Shell:\n"
        f"```\n{shown}\n```\n"
        "Open the conversation yourself: react to what the student is looking at, in the way your "
        "instructions describe. Do not say that the student reported, sent or asked anything."
    )


def install_error_report(command: str, output: str) -> str:
    """The first message after a failed package installation, like error_report().

    Thonny's package manager (Tools > Manage packages) sends PackageInstallFailed when pip
    fails (Jakub 2026-10-13, cortex-zamestnani#74: the assistant reacts to installation errors
    too). The output is pip's log as the dialog shows it; its end holds the error.
    """
    output = output.strip()
    if len(output) > SHELL_OUTPUT_LIMIT:
        output = "...\n" + output[-SHELL_OUTPUT_LIMIT:]
    return (
        "[Automatic notice from Thonny - the student did not type this.] The student tried to "
        "install a Python package with Thonny's package manager (Tools > Manage packages), and "
        f"the installation failed. Command: {command}\n"
        f"This is the output the student sees in the installation window:\n```\n{output}\n```\n"
        "Open the conversation yourself: react to what the student is looking at, in the way your "
        "instructions describe. Do not say that the student reported, sent or asked anything."
    )


def prepare_messages(
    messages: List[Dict[str, str]], max_messages: int = MAX_HISTORY_MESSAGES
) -> List[Dict[str, str]]:
    """Make a history acceptable to every provider.

    - keeps only the last ``max_messages`` messages (bounded cost),
    - merges consecutive messages with the same role (e.g. an auto-sent error
      after a cancelled answer),
    - drops empty messages and makes sure the history starts with a user turn.
    """
    merged: List[Dict[str, str]] = []
    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        role = "assistant" if msg.get("role") == "assistant" else "user"
        if merged and merged[-1]["role"] == role:
            merged[-1] = {"role": role, "content": merged[-1]["content"] + "\n\n" + content}
        else:
            merged.append({"role": role, "content": content})

    merged = merged[-max_messages:]
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


# What Ollama says when a model does not fit (its llm/status.go and the scheduler, 2026-10):
# "model requires more system memory (...) than is available (...)", "unable to load full
# model on GPU", or the runner's own "out of memory", "failed to allocate ..." and the like
OUT_OF_MEMORY_TEXTS = (
    "memory",
    "unable to load full model",
    "cudamalloc failed",
    "hipmalloc failed",
    "failed to allocate",
    "allocation failed",
    "failed to reserve graph",
)


def is_out_of_memory(exc: BaseException) -> bool:
    """Ollama could not load the model for lack of memory (its error text says so)."""
    text = str(exc).lower()
    return any(needle in text for needle in OUT_OF_MEMORY_TEXTS)


def _status(exc: BaseException) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if status is None:
        code = getattr(exc, "code", None)
        status = code if isinstance(code, int) else None
    return status


# Google answers a wrong Gemini key with 400 INVALID_ARGUMENT, not 401 (measured 2026-11-12,
# cortex-zamestnani#117: "API key not valid. Please pass a valid API key.", reason
# API_KEY_INVALID); Anthropic and OpenAI answer 401
REJECTED_KEY_TEXTS = ("api key not valid", "api_key_invalid")


def is_rejected_key(exc: BaseException) -> bool:
    """The provider refused the API key (a typo, a deleted or an expired key)."""
    name = type(exc).__name__
    if _status(exc) in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return True
    text = str(exc).lower()
    return any(needle in text for needle in REJECTED_KEY_TEXTS)


# Texts that say what the AI assistant needs, with a link to Tools > Options > AI Assistant
# in the panel (Jakub 2026-11-12, cortex-zamestnani#117: the student does not know what a
# provider is; no "ask your teacher", the options have a short guide for every choice)
def needs_text() -> str:
    return tr("The AI assistant needs an API key or a local model.")


def rejected_key_text() -> str:
    return tr(
        "The AI assistant's API key is not valid. The assistant needs a valid API key or a "
        "local model."
    )


def settings_link_text() -> str:
    return tr("Set up the AI assistant")


def error_opens_settings(exc: BaseException, provider_id: str = "") -> bool:
    """The problem behind friendly_error() is fixed in Tools > Options > AI Assistant."""
    if isinstance(exc, ImportError):
        return False
    return (
        is_rejected_key(exc)
        or _status(exc) == 404
        or "NotFound" in type(exc).__name__
        or (provider_id == "ollama" and is_out_of_memory(exc))
    )


def friendly_error(exc: BaseException, provider_id: str = "", model: str = "") -> str:
    """Turn an SDK exception into a short message a student can act on.

    Deliberately does not echo the raw text of auth errors, which some SDKs
    build from (partially masked) API keys.
    """
    status = _status(exc)
    name = type(exc).__name__

    if isinstance(exc, ImportError):
        return install_hint(provider_id)
    if is_rejected_key(exc):
        return rejected_key_text()
    if status == 404 or "NotFound" in name:
        return tr(
            "The model %s is not available. Choose another model in "
            "Tools > Options > AI Assistant."
        ) % model
    if provider_id == "ollama" and is_out_of_memory(exc):
        return tr(
            "The model %s does not fit into the computer's free memory. Close other programs, "
            "or choose a smaller model in Tools > Options > AI Assistant."
        ) % model
    if status == 429 or "RateLimit" in name:
        return tr(
            "Too many requests or the account's quota is used up. Wait a moment and try again."
        )
    if "Timeout" in name:
        return tr("The AI service did not answer in time. Try again.")
    if "Connect" in name or isinstance(exc, OSError):
        if provider_id == "ollama":
            from thonny.plugins.aia import local_ui

            if local_ui.handles(get_workbench()):
                # the application's local model (cortex-zamestnani#120): no Ollama in the text
                return tr("The local model does not answer. Close Thonny and start it again.")
            return tr(
                "Cannot reach Ollama. Is it running (ollama serve) and is the host correct?"
            )
        return tr("Cannot connect to the AI service. Check the internet connection.")

    text = str(exc).strip() or name
    if len(text) > 300:
        text = text[:300] + "..."
    return f"{name}: {text}"


class AIACore:
    """Provider registry and API key abstraction shared across Thonny versions."""

    def __init__(self):
        self._providers: Dict[str, LLMProvider] = {}

    def register_providers(self):
        """Register providers whose Python package is installed.

        Provider modules import their SDK lazily, so the check has to be done
        here explicitly - importing the provider module alone never fails.
        """
        wb = get_workbench()

        if package_installed("openai"):
            from thonny.plugins.aia.providers.openai_provider import OpenAIProvider

            self._providers["openai"] = OpenAIProvider(
                get_api_key=lambda: self.get_api_key("openai"),
                get_api_base=lambda: wb.get_option("aia.openai_api_base") or None,
            )

        if package_installed("anthropic"):
            from thonny.plugins.aia.providers.anthropic_provider import AnthropicProvider

            self._providers["anthropic"] = AnthropicProvider(
                get_api_key=lambda: self.get_api_key("anthropic"),
            )

        if package_installed("google.genai"):
            from thonny.plugins.aia.providers.gemini_provider import GeminiProvider

            self._providers["gemini"] = GeminiProvider(
                get_api_key=lambda: self.get_api_key("gemini"),
            )

        # Claude Code CLI: no Python package, a program on PATH (demo only, see provider)
        from thonny.plugins.aia.providers.claude_code_provider import (
            ClaudeCodeProvider,
            find_claude_executable,
        )

        claude_executable = find_claude_executable()
        if claude_executable:
            self._providers["claude_code"] = ClaudeCodeProvider(claude_executable)

        if package_installed("ollama"):
            from thonny.plugins.aia.providers.ollama_provider import OllamaProvider

            from thonny.plugins.aia.local_models import get_manager

            # An Ollama set by hand, else the one the application prepared (its own, or the
            # one on 11434 that has the model: local_models.py, cortex-zamestnani#120)
            self._providers["ollama"] = OllamaProvider(
                get_host=lambda: wb.get_option("aia.ollama_host") or get_manager().host(),
            )

        logger.debug("AIA providers available: %s", ", ".join(self._providers) or "none")

    def _get_stored_api_key(self, provider_name: str) -> Optional[str]:
        wb = get_workbench()
        if hasattr(wb, "get_secret"):
            key = wb.get_secret(f"aia_{provider_name}_api_key", None)
        else:
            key = wb.get_option(f"aia.{provider_name}_api_key")
        return key.strip() if key and key.strip() else None

    def has_stored_api_key(self, provider_name: str) -> bool:
        return self._get_stored_api_key(provider_name) is not None

    def get_api_key(self, provider_name: str) -> Optional[str]:
        """Stored key (secrets store if available, else plain option), then env var."""
        key = self._get_stored_api_key(provider_name)
        if key:
            return key
        for env_name in API_KEY_ENV_VARS.get(provider_name, ()):
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
        return None

    def set_api_key(self, provider_name: str, key: Optional[str]):
        """Store (or with an empty key, remove) the API key."""
        wb = get_workbench()
        key = (key or "").strip()
        if hasattr(wb, "set_secret"):
            wb.set_secret(f"aia_{provider_name}_api_key", key or None)
        else:
            wb.set_option(f"aia.{provider_name}_api_key", key)

    def get_current_provider_id(self) -> str:
        return get_workbench().get_option("aia.provider")

    def get_current_provider(self) -> Optional[LLMProvider]:
        return self._providers.get(self.get_current_provider_id())

    def get_current_model(self, provider: LLMProvider) -> str:
        """The model chosen for the provider in Tools > Options, else its default. UI thread.

        A model stored by an earlier version in aia.model is ignored.
        """
        for provider_id, registered in self._providers.items():
            if registered is provider and provider_id in MODEL_CHOICE_PROVIDERS:
                chosen = (get_workbench().get_option(model_option(provider_id)) or "").strip()
                if chosen:
                    return chosen
        return provider.get_default_model()

    def check_ready(self) -> Optional[str]:
        """Return None if the current provider can be used, else a user-facing reason."""
        problem = self.ready_problem()
        return problem[0] if problem else None

    def ready_problem(self) -> Optional[Tuple[str, bool]]:
        """None if the current provider can be used, else (reason, fixed in Tools > Options).

        No provider, an unknown one and a missing key all say what the AI assistant needs
        (Jakub 2026-11-12, cortex-zamestnani#117), the options then guide through every choice.
        """
        provider_id = self.get_current_provider_id()
        if not provider_id:
            return needs_text(), True
        provider = self._providers.get(provider_id)
        if provider is None:
            if provider_id in PROVIDER_PACKAGES:
                return install_hint(provider_id), False
            if provider_id == "claude_code":
                return (
                    tr(
                        "Claude Code (the 'claude' command) was not found. Install Claude Code, "
                        "sign in by running 'claude' once, then restart Thonny."
                    ),
                    False,
                )
            return needs_text(), True
        if not provider.is_available():
            return needs_text(), True
        if provider_id == "ollama":
            from thonny.plugins.aia import local_ui
            from thonny.plugins.aia.local_models import get_manager

            if local_ui.handles(get_workbench()):
                return local_ui.problem(get_manager().status())
        return None

    @property
    def providers(self) -> Dict[str, LLMProvider]:
        return self._providers
