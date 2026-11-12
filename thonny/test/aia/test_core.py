# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT

import sys
import types

import pytest


# --- pure helpers -------------------------------------------------------------


def test_prepare_messages_merges_trims_and_starts_with_user(fake_thonny):
    from thonny.plugins.aia._common import prepare_messages

    history = [
        {"role": "assistant", "content": "orphan answer"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "  "},
        {"role": "assistant", "content": "c"},
    ]
    assert prepare_messages(history) == [
        {"role": "user", "content": "a\n\nb"},
        {"role": "assistant", "content": "c"},
    ]

    long = [{"role": ("user", "assistant")[i % 2], "content": str(i)} for i in range(50)]
    trimmed = prepare_messages(long, max_messages=5)
    assert len(trimmed) <= 5 and trimmed[0]["role"] == "user"


def test_find_local_imports(fake_thonny, tmp_path):
    from thonny.plugins.aia import _common

    main = tmp_path / "main.py"
    main.write_text("import helper\nfrom pkg.sub import x\nimport os\nimport main\n", "utf-8")
    (tmp_path / "helper.py").write_text("X = 1\n", "utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "sub.py").write_text("x = 2\n", "utf-8")

    found = _common.find_local_imports(str(main), main.read_text("utf-8"))
    assert set(found) == {"helper.py", str(tmp_path.joinpath("pkg", "sub.py").relative_to(tmp_path))}
    assert _common.find_local_imports(str(main), "def (:") is None
    assert _common.find_local_imports(None, "import helper") is None


def test_find_local_imports_truncates_big_files(fake_thonny, tmp_path, monkeypatch):
    from thonny.plugins.aia import _common

    monkeypatch.setattr(_common, "MAX_IMPORTED_FILE_CHARS", 10)
    main = tmp_path / "main.py"
    main.write_text("import big\n", "utf-8")
    (tmp_path / "big.py").write_text("a" * 100, "utf-8")
    content = _common.find_local_imports(str(main), "import big\n")["big.py"]
    assert content.startswith("a" * 10) and "truncated" in content


class _StatusError(Exception):
    def __init__(self, status_code, text="secret sk-proj-abcdef"):
        super().__init__(text)
        self.status_code = status_code


def test_friendly_error_hides_auth_details(fake_thonny):
    from thonny.plugins.aia._common import friendly_error

    msg = friendly_error(_StatusError(401), "openai", "gpt-4o-mini")
    assert "not valid" in msg and "local model" in msg and "sk-" not in msg
    assert "Choose another model" in friendly_error(_StatusError(404), "anthropic", "old-model")
    assert "quota" in friendly_error(_StatusError(429))
    assert "Ollama" in friendly_error(ConnectionRefusedError("x"), "ollama")
    assert "'google.genai', which is missing" in friendly_error(ImportError("x"), "gemini")


def test_build_system_prompt_includes_context(fake_thonny):
    from thonny.plugins.aia.prompts import SOCRATIC_PROMPT, build_system_prompt

    assert build_system_prompt("unknown-level").startswith(SOCRATIC_PROMPT)
    prompt = build_system_prompt(
        "direct", current_code="print(x)", current_file_name="a.py", imported_files={"b.py": "y=1"}
    )
    assert "a.py" in prompt and "print(x)" in prompt and "b.py" in prompt


def test_local_model_gets_the_rules_again_at_the_end(fake_thonny):
    from thonny.plugins.aia import prompts

    cloud = prompts.build_system_prompt(
        "socratic", current_code="print(x)", current_file_name="a.py", language="Čeština"
    )
    local = prompts.build_system_prompt(
        "socratic", current_code="print(x)", current_file_name="a.py", local=True, language="Čeština"
    )
    assert local.startswith(cloud)  # cloud providers get the prompt unchanged
    tail = local[len(cloud):]
    assert "Reply in Čeština" in tail and "tykání" in tail and "Do NOT give the fix" in tail
    assert "Do NOT give the fix" not in prompts.build_system_prompt("explanatory", local=True)
    assert "the language of the student's own messages" in prompts.build_system_prompt("direct", local=True)


def test_every_provider_gets_the_interface_language(fake_thonny):
    from thonny.plugins.aia import prompts

    cloud = prompts.build_system_prompt("socratic", language="Čeština")
    assert "Thonny is set to Čeština. Reply in Čeština" in cloud
    assert "RULES THAT MATTER MOST" not in cloud  # the local rules stay local
    assert "Thonny is set to" not in prompts.build_system_prompt("socratic")


def test_every_style_and_provider_is_told_to_speak_informally(fake_thonny):
    from thonny.plugins.aia import prompts

    for style in prompts.ANSWER_STYLES:
        prompt = prompts.build_system_prompt(style, language="Čeština")
        assert prompts.ADDRESS_RULE in prompt  # with the style
        assert prompt.rstrip().endswith(prompts.ADDRESS_REMINDER.rstrip())  # and last of all
        assert "never vykání" in prompt


# --- API keys and provider registry --------------------------------------------


def test_api_key_secret_store_and_env_fallback(fake_thonny, monkeypatch):
    from thonny.plugins.aia._common import AIACore

    core = AIACore()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert core.get_api_key("anthropic") is None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert core.get_api_key("anthropic") == "env-key"
    assert not core.has_stored_api_key("anthropic")

    core.set_api_key("anthropic", "  stored-key ")
    assert fake_thonny.secrets["aia_anthropic_api_key"] == "stored-key"
    assert core.get_api_key("anthropic") == "stored-key"

    core.set_api_key("anthropic", None)
    assert core.get_api_key("anthropic") == "env-key"


def test_api_key_plain_option_without_secret_store(fake_thonny, monkeypatch):
    from thonny.plugins.aia._common import AIACore

    del fake_thonny.get_secret, fake_thonny.set_secret
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    core = AIACore()
    core.set_api_key("openai", "k")
    assert fake_thonny.options["aia.openai_api_key"] == "k"
    assert core.get_api_key("openai") == "k"


def test_register_providers_skips_missing_packages(fake_thonny, monkeypatch):
    from thonny.plugins.aia import _common
    from thonny.plugins.aia.providers import claude_code_provider

    monkeypatch.setattr(_common, "package_installed", lambda name: name == "ollama")
    monkeypatch.setattr(claude_code_provider, "find_claude_executable", lambda: None)
    fake_thonny.options["aia.provider"] = "anthropic"
    core = _common.AIACore()
    core.register_providers()
    assert set(core.providers) == {"ollama"}
    assert "Reinstall Thonny AIA" in core.check_ready()


def test_no_provider_chosen_asks_for_the_choice(fake_thonny):
    """The welcome dialog's "I will set it up later" leaves aia.provider empty."""
    from thonny.plugins.aia._common import AIACore

    fake_thonny.options["aia.provider"] = ""
    core = AIACore()
    core.register_providers()
    assert core.get_current_provider() is None
    assert core.check_ready() == "The AI assistant needs an API key or a local model."
    assert core.ready_problem()[1] is True  # the panel links to the options


def test_model_is_chosen_per_provider(fake_thonny, monkeypatch):
    """Tools > Options chooses the model per provider (Jakub 2026-10-13, #75); "" = default."""
    from thonny.plugins.aia import _common
    from thonny.plugins.aia._common import AIACore

    monkeypatch.setattr(_common, "package_installed", lambda name: True)
    core = AIACore()
    core.register_providers()
    anthropic, gemini = core.providers["anthropic"], core.providers["gemini"]
    fake_thonny.options["aia.model"] = "claude-sonnet-4-20250514"  # stored by old versions
    assert core.get_current_model(anthropic) == "claude-haiku-5-5"
    fake_thonny.options["aia.anthropic_model"] = "claude-opus-5-5"
    assert core.get_current_model(anthropic) == "claude-opus-5-5"
    assert core.get_current_model(gemini) == "gemini-3.5-flash-lite"  # its own choice
    # the local model too (Jakub 2026-10-16, #80); "" = gpt-oss:20b, also before any choice
    assert core.get_current_model(core.providers["ollama"]) == "gpt-oss:20b"
    fake_thonny.options["aia.ollama_model"] = ""
    assert core.get_current_model(core.providers["ollama"]) == "gpt-oss:20b"
    fake_thonny.options["aia.ollama_model"] = "qwen3:14b"
    assert core.get_current_model(core.providers["ollama"]) == "qwen3:14b"


def test_anthropic_lists_the_models_of_the_key(fake_thonny, monkeypatch):
    from thonny.plugins.aia.providers.anthropic_provider import AnthropicProvider

    seen = {}

    class FakeAnthropic:
        def __init__(self, api_key, timeout, max_retries):
            seen.update(api_key=api_key, timeout=timeout)
            self.models = types.SimpleNamespace(
                list=lambda: [types.SimpleNamespace(id=i) for i in
                              ("claude-opus-5-5", "claude-haiku-5-5", "claude-sonnet-5-5")]
            )

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    models = AnthropicProvider(get_api_key=lambda: "k").list_models(timeout=3)
    assert models == ["claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5"]  # default first
    assert seen == {"api_key": "k", "timeout": 3}


def test_openai_lists_only_chat_models_newest_first(fake_thonny, monkeypatch):
    from thonny.plugins.aia.providers.openai_provider import OpenAIProvider

    listed = [("gpt-6-astra", 3), ("text-embedding-3-small", 9), ("gpt-6-luna", 1),
              ("gpt-6-realtime", 8), ("whisper-1", 7), ("o4-mini", 2), ("gpt-6.1-sol", 4)]

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.models = types.SimpleNamespace(
                list=lambda: [types.SimpleNamespace(id=i, created=c) for i, c in listed]
            )

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    models = OpenAIProvider(get_api_key=lambda: "k").list_models()
    assert models == ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra", "o4-mini"]


def test_gemini_lists_only_text_chat_models(fake_thonny, monkeypatch):
    from thonny.plugins.aia.providers.gemini_provider import GeminiProvider

    listed = [("models/gemini-3.8-flash", ["generateContent", "countTokens"]),
              ("models/gemini-embedding-002", ["embedContent"]),
              ("models/gemini-3.8-flash-tts", ["generateContent"]),
              ("models/gemini-3.5-flash-lite", ["generateContent"]),
              ("models/veo-4", ["predictLongRunning"])]

    class FakeClient:
        def __init__(self, api_key, http_options):
            self.models = types.SimpleNamespace(
                list=lambda: [types.SimpleNamespace(name=n, supported_actions=a) for n, a in listed]
            )

    genai = types.SimpleNamespace(Client=FakeClient)
    gtypes = types.SimpleNamespace(HttpOptions=lambda timeout: timeout)
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", gtypes)
    genai.types = gtypes
    assert GeminiProvider(get_api_key=lambda: "k").list_models() == [
        "gemini-3.5-flash-lite", "gemini-3.8-flash"
    ]


# --- providers with fake SDKs ---------------------------------------------------


def _chunk(content):
    delta = types.SimpleNamespace(content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)])


def test_openai_provider_skips_empty_choices_and_closes_stream(fake_thonny, monkeypatch):
    closed = []

    class FakeStream:
        def __iter__(self):
            yield types.SimpleNamespace(choices=[])  # Azure content-filter chunk
            yield _chunk("Hel")
            yield _chunk(None)
            yield _chunk("lo")

        def close(self):
            closed.append(True)

    created = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=lambda **kw: FakeStream())
            )

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    from thonny.plugins.aia.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(get_api_key=lambda: "k", get_api_base=lambda: "http://proxy")
    text = "".join(provider.stream_chat("sys", [{"role": "user", "content": "hi"}], "m"))
    assert text == "Hello"
    assert closed == [True]
    assert created["base_url"] == "http://proxy" and created["timeout"] > 0


def _fake_ollama(monkeypatch, chunks, closed, calls):
    class FakeStream:
        def __iter__(self):
            yield from chunks

        def close(self):
            closed.append(True)

    class FakeClient:
        def __init__(self, host=None, timeout=None, **kwargs):
            calls.append({"host": host, "timeout": timeout, "client_kwargs": kwargs})

        def chat(self, **kwargs):
            calls[-1].update(kwargs)
            return FakeStream()

        def generate(self, **kwargs):
            calls[-1].update(kwargs, request="generate")

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClient))


def test_ollama_provider_streams_and_closes_stream(fake_thonny, monkeypatch):
    closed, calls = [], []
    chunks = [
        {"message": {"role": "assistant", "content": "Ahoj"}},
        {"message": {"role": "assistant", "content": ""}},  # e.g. the final "done" chunk
        {"message": {"role": "assistant", "content": ", světe"}},
    ]
    _fake_ollama(monkeypatch, chunks, closed, calls)
    from thonny.plugins.aia.providers.ollama_provider import OllamaProvider

    provider = OllamaProvider(get_host=lambda: "http://pc:11434")
    text = "".join(provider.stream_chat("sys", [{"role": "user", "content": "hi"}], "m"))
    assert text == "Ahoj, světe"
    assert closed == [True]
    assert calls[0]["host"] == "http://pc:11434" and calls[0]["timeout"] > 0
    assert calls[0]["stream"] is True and calls[0]["model"] == "m"
    assert calls[0]["think"] is False  # no silent reasoning before the first token
    assert calls[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert calls[0]["client_kwargs"] == {}  # a remote server may need the proxy
    assert calls[0]["keep_alive"] == "10m"  # every request renews the loaded model


def test_ollama_provider_closes_stream_when_consumer_stops(fake_thonny, monkeypatch):
    closed, calls = [], []
    chunks = [{"message": {"content": "a"}}, {"message": {"content": "b"}}]
    _fake_ollama(monkeypatch, chunks, closed, calls)
    from thonny.plugins.aia.providers.ollama_provider import OllamaProvider

    stream = OllamaProvider().stream_chat("sys", [], "m")
    assert next(stream) == "a"
    stream.close()  # what the panel does on Cancel
    assert closed == [True]
    assert calls[0]["host"] is None  # no host set -> OLLAMA_HOST or localhost
    assert calls[0]["client_kwargs"] == {"trust_env": False}  # no school proxy for localhost


def test_ollama_gpt_oss_gets_lowest_thinking_level(fake_thonny, monkeypatch):
    from thonny.plugins.aia.providers.ollama_provider import think_setting

    assert think_setting("gpt-oss:20b") == "low"  # ignores False, only levels work
    assert think_setting("library/gpt-oss") == "low"
    assert think_setting("gemma4:e2b-it-qat") is False
    assert think_setting("qwen3:4b") is False


def test_ollama_local_host_detection(fake_thonny):
    from thonny.plugins.aia.providers.ollama_provider import is_local_host

    for host in (None, "", "localhost:11434", "http://127.0.0.1:11435", "http://[::1]:11434", "127.0.1.1"):
        assert is_local_host(host), host
    for host in ("http://pc:11434", "server.skola.local", "http://10.0.0.5:11434"):
        assert not is_local_host(host), host


def _fake_anthropic(monkeypatch, calls, stop_reason="end_turn"):
    class FakeStream:
        text_stream = iter(["Ahoj", "!"])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append("closed")

        def get_final_message(self):
            return types.SimpleNamespace(stop_reason=stop_reason)

    def stream(kind):
        def _stream(**kwargs):
            calls.append((kind, kwargs))
            return FakeStream()

        return _stream

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = types.SimpleNamespace(stream=stream("plain"))
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream("beta")))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))


def test_anthropic_provider_uses_refusal_fallback_only_where_supported(fake_thonny, monkeypatch):
    calls = []
    _fake_anthropic(monkeypatch, calls)
    from thonny.plugins.aia.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(get_api_key=lambda: "k")
    assert "".join(provider.stream_chat("sys", [], "claude-sonnet-5-5")) == "Ahoj!"
    kind, kwargs = calls[0]
    assert kind == "beta" and kwargs["extra_body"] == {"fallbacks": "default"}
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"] and kwargs["system"] == "sys"
    assert calls[1] == "closed"

    calls.clear()
    _fake_anthropic(monkeypatch, calls)
    "".join(provider.stream_chat("sys", [], "claude-haiku-5-5"))
    assert calls[0][0] == "plain" and "extra_body" not in calls[0][1]


def test_anthropic_provider_tells_the_student_about_a_refusal(fake_thonny, monkeypatch):
    calls = []
    _fake_anthropic(monkeypatch, calls, stop_reason="refusal")
    from thonny.plugins.aia.providers.anthropic_provider import REFUSAL_NOTE, AnthropicProvider

    text = "".join(AnthropicProvider(get_api_key=lambda: "k").stream_chat("s", [], "claude-sonnet-5-5"))
    assert text == "Ahoj!" + REFUSAL_NOTE


def test_frozen_provider_reads_settings_once(fake_thonny):
    from thonny.plugins.aia.providers.openai_provider import OpenAIProvider

    fake_thonny.options["aia.openai_api_base"] = "http://a"
    live = OpenAIProvider(
        get_api_key=lambda: "k1", get_api_base=lambda: fake_thonny.options["aia.openai_api_base"]
    )
    frozen = live.frozen()
    fake_thonny.options["aia.openai_api_base"] = "http://b"
    assert frozen._get_api_base() == "http://a" and frozen._get_api_key() == "k1"
    assert live._get_api_base() == "http://b"


# --- load_plugin routing ----------------------------------------------------------


def test_answer_styles_renamed_and_old_values_migrated(fake_thonny, monkeypatch):
    import thonny.plugins.aia as aia
    from thonny.plugins.aia import prompts

    assert prompts.build_system_prompt("mentor").startswith(prompts.SOCRATIC_PROMPT)
    assert prompts.build_system_prompt("balanced").startswith(prompts.EXPLANATORY_PROMPT)
    assert prompts.build_system_prompt("direct").startswith(prompts.DIRECT_PROMPT)

    monkeypatch.setattr(aia, "_load_panel", lambda wb: None)
    for stored, expected in (("balanced", "explanatory"), ("mentor", "socratic"), ("direct", "direct")):
        fake_thonny.options["aia.mentoring_level"] = stored
        aia.load_plugin()
        assert fake_thonny.options["aia.mentoring_level"] == expected

    del fake_thonny.options["aia.mentoring_level"]
    aia.load_plugin()
    assert fake_thonny.options["aia.mentoring_level"] == "socratic"  # default for a new user


def test_provider_display_name(fake_thonny):
    from thonny.plugins.aia._common import provider_display_name

    assert provider_display_name("openai") == "OpenAI"  # not "Openai" from str.title()
    assert provider_display_name("ollama") == "Ollama"
    assert provider_display_name("custom") == "custom"


def test_ollama_lists_its_local_chat_models(fake_thonny, monkeypatch):
    """Tools > Options offers the models Ollama has (#80): no cloud and no embedding models."""
    calls = []

    class FakeClient:
        def __init__(self, host=None, timeout=None, **kwargs):
            calls.append({"host": host, "timeout": timeout, "client_kwargs": kwargs})

        def list(self):
            return {"models": [
                {"model": "qwen3:14b", "size": 9_300_000_000},
                {"model": "nomic-embed-text:latest", "size": 274_000_000},
                {"model": "gpt-oss:120b-cloud", "size": 384},
                {"model": "glm-4.6:cloud", "size": 400},
                {"model": "gpt-oss:20b", "size": 13_793_441_566},
                {"model": "Gemma3:4b", "size": 3_300_000_000},
                {"model": "old-ollama:7b", "size": 4_000_000_000},
            ]}

        def show(self, model):
            if model.startswith("old-ollama"):
                return {}  # an older Ollama gives no capabilities
            if model.startswith("nomic-embed"):
                return {"capabilities": ["embedding"]}
            return {"capabilities": ["completion", "tools"]}

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClient))
    from thonny.plugins.aia.providers.ollama_provider import OllamaProvider

    provider = OllamaProvider(get_host=lambda: "http://127.0.0.1:11435")
    assert provider.list_models() == ["gpt-oss:20b", "Gemma3:4b", "old-ollama:7b", "qwen3:14b"]
    assert calls[0]["host"] == "http://127.0.0.1:11435"
    assert calls[0]["client_kwargs"] == {"trust_env": False}  # no school proxy for localhost

    # without the default model nothing is added: the page offers it as the default
    FakeClient.list = lambda self: {"models": [{"model": "qwen3:14b", "size": 9_300_000_000}]}
    assert provider.list_models() == ["qwen3:14b"]
    # the Options page offers no list without asking Ollama (no request on the UI thread)
    assert provider.get_available_models() == ["gpt-oss:20b"]


def test_ollama_cloud_models_are_recognized(fake_thonny):
    from thonny.plugins.aia.providers.ollama_provider import is_cloud_model

    for name in ("gpt-oss:120b-cloud", "glm-4.6:cloud", "qwen3-coder:480b-cloud"):
        assert is_cloud_model(name), name
    assert is_cloud_model("some-remote:latest", size=384)
    for name in ("gpt-oss:20b", "gemma3", "cloudy:7b", "qwen3:14b"):
        assert not is_cloud_model(name, size=5_000_000_000), name
    assert not is_cloud_model("gpt-oss:20b")  # size unknown


def test_friendly_error_when_the_local_model_does_not_fit(fake_thonny):
    """Ollama's 500 for a model that does not fit (cortex-zamestnani#80): no raw English error."""
    from thonny.plugins.aia._common import friendly_error

    for text in (
        "model requires more system memory (13.1 GiB) than is available (9.5 GiB)",
        "llama runner process has terminated: cudaMalloc failed: out of memory",
        "unable to load full model on GPU",
    ):
        msg = friendly_error(_StatusError(500, text), "ollama", "gpt-oss:20b")
        assert "does not fit" in msg and "gpt-oss:20b" in msg and "GiB" not in msg
    # a model that is not pulled stays a 404
    assert "Choose another model" in friendly_error(_StatusError(404, "model not found"), "ollama", "x")
    # other providers keep their texts
    assert "does not fit" not in friendly_error(_StatusError(500, "memory"), "openai", "x")


def test_rejected_keys_of_all_providers_are_recognized(fake_thonny):
    """Measured 2026-11-12 (#117): Anthropic and OpenAI answer a wrong key with 401, Google
    with 400 INVALID_ARGUMENT "API key not valid"; until beta 8 the panel showed Google's raw
    error."""
    from thonny.plugins.aia._common import error_opens_settings, friendly_error, is_rejected_key

    class ClientError(Exception):
        code = 400

    gemini = ClientError(
        "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'API key not valid. Please "
        "pass a valid API key.', 'status': 'INVALID_ARGUMENT', 'details': [{'reason': "
        "'API_KEY_INVALID'}]}}"
    )
    for exc in (_StatusError(401), _StatusError(403), gemini):
        assert is_rejected_key(exc)
        assert error_opens_settings(exc, "gemini")
        assert "API key is not valid" in friendly_error(exc, "gemini", "m")
    other = ClientError("400 INVALID_ARGUMENT. {'message': 'Request contains an invalid argument.'}")
    assert not is_rejected_key(other)
    assert not is_rejected_key(_StatusError(429))
    assert not error_opens_settings(ConnectionError("x"), "openai")
    assert error_opens_settings(_StatusError(404), "anthropic")
    assert not error_opens_settings(ImportError("x"), "gemini")


def test_missing_key_and_unknown_provider_say_what_is_needed(fake_thonny):
    from thonny.plugins.aia._common import AIACore

    core = AIACore()
    core.register_providers()
    fake_thonny.options["aia.provider"] = "nonsense"
    assert core.ready_problem() == ("The AI assistant needs an API key or a local model.", True)


def test_no_text_sends_the_student_to_a_teacher():
    """Jakub 2026-11-12 (#117): no "ask your teacher" anywhere in AIA's texts."""
    import ast
    import pathlib

    import thonny

    root = pathlib.Path(thonny.__file__).parent
    files = [root / "first_run.py", *sorted((root / "plugins" / "aia").rglob("*.py"))]
    for path in files:
        if "providers" in path.parts:
            continue  # the Claude Code demo provider speaks of the teacher's own account
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):  # the texts, not the comments explaining why
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "teacher" not in node.value.lower(), (path.name, node.value)
