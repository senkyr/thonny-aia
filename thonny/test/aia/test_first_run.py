# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""The AI choice of the welcome dialog, also used by Tools > Options > AI Assistant."""

import os

import pytest


def test_later_is_first_and_preselected_local_model_last(fake_thonny):
    from thonny import first_run

    models = first_run._default_models()
    # Jakub 2026-10-13, cortex-zamestnani#76: "later" and the local model swapped places
    assert list(first_run._ai_labels(models)) == ["later", "anthropic", "gemini", "openai", "ollama"]
    assert first_run.AI_DEFAULT == first_run.AI_LATER


def test_the_local_choice_names_the_chosen_model(fake_thonny):
    """#80: "Lokální model (qwen3:14b)" (no Ollama since #120); sizes and speed only for gpt-oss:20b."""
    from thonny import first_run

    models = dict(first_run._default_models(), ollama="qwen3:14b")
    assert first_run._ai_labels(models)["ollama"] == "Local model (qwen3:14b)"


# --- the welcome dialog once more after an update (cortex-zamestnani#118, #120) ---


class _Conf:
    def __init__(self, **options):
        self.options = {k.replace("__", "."): v for k, v in options.items()}

    def get_option(self, name, default=None):
        return self.options.get(name, default)


@pytest.fixture
def first_run(fake_thonny, monkeypatch, tmp_path):
    from thonny import first_run as module
    from thonny.plugins.aia import local_models

    monkeypatch.setenv("THONNY_AIA_LOCAL_MODELS", str(tmp_path))
    monkeypatch.setattr(local_models, "has_model", lambda host, model, timeout=None: False)
    for names in module_env_keys():
        monkeypatch.delenv(names, raising=False)
    return module


def module_env_keys():
    from thonny.plugins.aia._common import API_KEY_ENV_VARS

    return [name for names in API_KEY_ENV_VARS.values() for name in names]


def test_later_and_missing_key_are_not_working(first_run):
    assert not first_run.ai_works(_Conf(aia__provider=""))
    assert not first_run.ai_works(_Conf())
    assert not first_run.ai_works(_Conf(aia__provider="anthropic"))
    assert first_run.ai_works(_Conf(aia__provider="anthropic", aia__anthropic_api_key="sk-x"))


def test_key_from_the_environment_works(first_run, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert first_run.ai_works(_Conf(aia__provider="gemini"))


def test_local_model_works_only_when_downloaded(first_run, monkeypatch, tmp_path):
    from thonny.plugins.aia import local_models

    conf = _Conf(aia__provider="ollama")
    assert not first_run.ai_works(conf)
    path = local_models.manifest_path("gpt-oss:20b", str(tmp_path))
    os.makedirs(os.path.dirname(path))
    open(path, "w").close()
    assert first_run.ai_works(conf)  # the application's own has the default model
    assert not first_run.ai_works(_Conf(aia__provider="ollama", aia__ollama_model="qwen3:14b"))
    monkeypatch.setattr(local_models, "has_model", lambda host, model, timeout=None: True)
    assert first_run.ai_works(_Conf(aia__provider="ollama", aia__ollama_model="qwen3:14b"))
    # an Ollama set by hand is not the dialog's business
    assert first_run.ai_works(_Conf(aia__provider="ollama", aia__ollama_host="http://10.0.0.5:11434"))


def test_aia_switched_off_gets_no_dialog(first_run):
    assert first_run.ai_works(_Conf(aia__enabled=False, aia__provider=""))


def test_once_per_version(first_run):
    conf = _Conf(aia__provider="")
    assert first_run.needs_welcome_again(conf, "5.5.0b9")
    conf.options["aia.welcome_version"] = "5.5.0b9"
    assert not first_run.needs_welcome_again(conf, "5.5.0b9")
    assert first_run.needs_welcome_again(conf, "5.5.0b10")
    conf.options["aia.anthropic_api_key"] = "sk-x"
    conf.options["aia.provider"] = "anthropic"
    assert not first_run.needs_welcome_again(conf, "5.5.0b10")
