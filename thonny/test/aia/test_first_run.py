# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""The AI choice of the welcome dialog, also used by Tools > Options > AI Assistant."""


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

    note = first_run._local_note("no_model", "gpt-oss:20b")
    assert "'ollama pull gpt-oss:20b' (13 GB)" in note and "14 GB" in note
    for state in ("missing", "stopped", "no_model", "ready"):
        note = first_run._local_note(state, "qwen3:14b")
        assert "GB" not in note and "20-70" not in note, state
    assert "'ollama pull qwen3:14b'." in first_run._local_note("no_model", "qwen3:14b")
