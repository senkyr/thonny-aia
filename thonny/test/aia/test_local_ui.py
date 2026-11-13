# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""What the panel and the options say about the prepared local model, and the wiring
(cortex-zamestnani#120)."""

import pytest

from conftest import ReadyLocalModels


@pytest.fixture
def ui(fake_thonny):
    from thonny.plugins.aia import local_ui

    fake_thonny.options.update({"aia.enabled": True, "aia.provider": "ollama", "general.language": "en_US"})
    return local_ui


def test_sizes_in_decimal_gb(ui, fake_thonny):
    assert ui.gb(13_793_441_566) == "13.8 GB"
    fake_thonny.options["general.language"] = "cs_CZ"
    assert ui.gb(13_793_441_566) == "13,8 GB"
    assert ui.gb(250 * 10**9) == "250 GB"
    assert ui.gb_ram(16 * 1024**3) == "16 GB"


def test_remaining_time(ui):
    assert ui.duration(None) is None
    assert ui.duration(40) == "1 min"
    assert ui.duration(6 * 60) == "6 min"
    assert ui.duration(80 * 60) == "1 h 20 min"


def test_progress_text(ui):
    status = {"phase": "downloading", "stage": "model", "done": 3.2e9, "total": 13.8e9, "eta": 360}
    assert ui.preparing_text(status) == "The assistant is getting ready... 3.2 GB of 13.8 GB, about 6 min left"
    status["eta"] = 20
    assert ui.preparing_text(status).endswith("13.8 GB, less than a minute left")
    status["eta"] = None
    assert ui.preparing_text(status) == "The assistant is getting ready... 3.2 GB of 13.8 GB"
    assert ui.preparing_text({"stage": "ollama"}) == "The assistant is getting ready..."


@pytest.mark.parametrize(
    "status, settings",
    [
        ({"phase": "consent"}, True),
        ({"phase": "weak", "ram": 8 * 1024**3}, True),
        ({"phase": "paused", "reason": "user", "done": 1e9, "total": 2e9}, True),
        ({"phase": "paused", "reason": "network"}, True),
        ({"phase": "failed", "reason": "signature"}, False),
        ({"phase": "failed", "reason": "server"}, True),
        ({"phase": "downloading", "stage": "model", "done": 0, "total": 1e9}, False),
        ({"phase": "checking"}, False),
    ],
)
def test_problems(ui, status, settings):
    text, opens = ui.problem(status)
    assert text and opens == settings
    for word in ("Ollama", "server", "port"):
        assert word not in text


def test_no_problem_when_ready(ui):
    for phase in ("ready", "system", "off"):
        assert ui.problem({"phase": phase}) is None


def test_consent_note(ui):
    info = {"model": "gpt-oss:20b", "size": 13_793_441_566, "need": 13_793_441_566,
            "free": 120 * 10**9, "ram": 32 * 1024**3, "ram_class": "fine", "enough_disk": True}
    assert ui.consent_text(info).startswith("13.8 GB will be downloaded, you have 120 GB free on the disk.")
    assert ui.can_prepare(info)
    info.update(ram=16 * 1024**3, ram_class="slow")
    assert ui.consent_text(info).endswith("This computer has 16 GB of memory: the local model will be slow.")
    info.update(free=10 * 10**9, enough_disk=False)
    assert "only 10.0 GB is free" in ui.consent_text(info) and not ui.can_prepare(info)
    info.update(ram=8 * 1024**3, ram_class="weak")
    assert "at least 16 GB" in ui.consent_text(info) and not ui.can_prepare(info)


def test_chosen_model(ui, fake_thonny):
    from thonny.plugins.aia._common import AIACore

    core = AIACore()
    core._providers["ollama"] = type("P", (), {"get_default_model": lambda self: "gpt-oss:20b"})()
    assert ui.chosen_model(fake_thonny, core) == "gpt-oss:20b"
    fake_thonny.options["aia.ollama_model"] = "qwen3:14b"
    assert ui.chosen_model(fake_thonny, core) == "qwen3:14b"
    fake_thonny.options["aia.ollama_host"] = "http://192.168.1.5:11434"
    assert ui.chosen_model(fake_thonny, core) is None  # set by hand: used as before
    del fake_thonny.options["aia.ollama_host"]
    fake_thonny.options["aia.provider"] = "anthropic"
    assert ui.chosen_model(fake_thonny, core) is None


def test_ready_problem_follows_the_local_model(fake_thonny, monkeypatch):
    from thonny.plugins.aia import _common, local_models

    fake_thonny.options.update({"aia.enabled": True, "aia.provider": "ollama"})
    core = _common.AIACore()
    core._providers["ollama"] = type("P", (), {"is_available": lambda self: True})()
    monkeypatch.setattr(local_models, "_manager", ReadyLocalModels("consent"))
    text, opens = core.ready_problem()
    assert text == "The local model is not downloaded yet." and opens
    monkeypatch.setattr(local_models, "_manager", ReadyLocalModels("system"))
    assert core.ready_problem() is None
    # an Ollama set by hand is asked directly, as before
    monkeypatch.setattr(local_models, "_manager", ReadyLocalModels("consent"))
    fake_thonny.options["aia.ollama_host"] = "http://127.0.0.1:11434"
    assert core.ready_problem() is None


def test_keeper_waits_for_the_prepared_model(fake_thonny, monkeypatch):
    from thonny.plugins.aia import local_model, local_models

    fake_thonny.options.update({"aia.enabled": True, "aia.provider": "ollama"})
    provider = type(
        "P", (), {"frozen": lambda self: self, "get_default_model": lambda self: "gpt-oss:20b"}
    )()
    core = type("C", (), {"providers": {"ollama": provider}, "get_current_model": lambda self, p: "gpt-oss:20b"})()
    keeper = local_model.LocalModelKeeper(fake_thonny, core)
    monkeypatch.setattr(local_models, "_manager", ReadyLocalModels("downloading"))
    assert keeper.wanted() is None
    monkeypatch.setattr(local_models, "_manager", ReadyLocalModels("ready"))
    assert keeper.wanted() == (provider, "gpt-oss:20b")
