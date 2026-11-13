# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""AIA - AI Mentor Assistant, built into Thonny AIA.

A pedagogical programming buddy that helps beginners learn Python through
dialogue rather than giving ready-made solutions. It takes the place of the
rule-based Assistant: when a program fails, AIA explains the error.

Originally the separate plugin ``thonny-aia`` (``thonnycontrib.aia``); in this
fork it lives in ``thonny.plugins.aia`` and only the panel layer is needed,
because Thonny 5 has no Chat API.
"""

import logging

from thonny import get_workbench
from thonny.plugins.aia._common import MODEL_CHOICE_PROVIDERS, model_option
from thonny.plugins.aia.prompts import DEFAULT_STYLE, answer_style

logger = logging.getLogger(__name__)


def load_plugin():
    """Entry point called by Thonny's plugin loader."""
    wb = get_workbench()

    wb.set_default("aia.enabled", True)
    # "" = not chosen yet: the welcome dialog's "I will set it up later" saves no provider
    # (Jakub 2026-10-10), the panel then asks for the choice
    wb.set_default("aia.provider", "")
    wb.set_default("aia.model", "")  # no longer used: the model is chosen per provider
    for provider_id in MODEL_CHOICE_PROVIDERS:
        wb.set_default(model_option(provider_id), "")  # "" = the provider's default
    wb.set_default("aia.mentoring_level", DEFAULT_STYLE)
    wb.set_default("aia.auto_attach_context", True)
    wb.set_default("aia.include_local_imports", False)
    wb.set_default("aia.openai_api_base", "")
    wb.set_default("aia.ollama_host", "")
    # the version whose welcome dialog was shown last (cortex-zamestnani#118: once more after
    # an update for those without a working AI, thonny.first_run.needs_welcome_again)
    wb.set_default("aia.welcome_version", "")

    # Answer styles were renamed on 2026-10-10 (mentor -> socratic, balanced -> explanatory)
    stored = wb.get_option("aia.mentoring_level")
    if stored != answer_style(stored):
        wb.set_option("aia.mentoring_level", answer_style(stored))

    _load_panel(wb)
    logger.info("AIA loaded")


def _load_panel(wb):
    """Register the AIA view, its configuration page and the Assistant replacement."""
    # Thonny 5 has no secrets store, API keys live in plain options (configuration.ini).
    wb.set_default("aia.openai_api_key", "")
    wb.set_default("aia.anthropic_api_key", "")
    wb.set_default("aia.gemini_api_key", "")

    from thonny.languages import tr
    from thonny.plugins.aia._panel.config_page import AIAPanelConfigPage
    from thonny.plugins.aia._panel.switching import install_switching_logic
    from thonny.plugins.aia._panel.view import AIAView

    from thonny.plugins.aia._panel.quadrants import init_option

    init_option(wb)  # the default layout in quadrants, only for a fresh configuration
    wb.add_view(AIAView, tr("AI Assistant"), "ne", visible_by_default=False)
    wb.add_configuration_page("aia", tr("AI Assistant"), AIAPanelConfigPage, 81)
    install_switching_logic(wb)

    # Ollama: load the model at startup so the first error explanation does not wait for it
    from thonny.plugins.aia._common import AIACore
    from thonny.plugins.aia.local_model import LocalModelKeeper

    core = AIACore()
    core.register_providers()
    # The local model that the application prepares by itself (cortex-zamestnani#120)
    from thonny.plugins.aia import local_ui

    local_ui.install(wb, core)
    LocalModelKeeper(wb, core).install()
