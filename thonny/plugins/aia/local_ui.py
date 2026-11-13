# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""The local model that Thonny AIA prepares (local_models.py) in the workbench: what the
panel, Tools > Options and the welcome dialog say about it, and the wiring (cortex-zamestnani#120).

The texts never name Ollama, a server or a port (Jakub 2026-11-12, cortex-zamestnani#118): the
student chooses "the local model", how it runs is the application's business.

Wiring (UI thread): the manager follows the options (AI on, the local model chosen, no Ollama
address set by hand), Thonny's close stops it, and a poll every POLL_MS sends the workbench
event ``AIALocalModel`` when its status changes; the keeper, the panel and the options page
listen to it. A download stopped by a lost connection is tried again every RETRY_MS.
"""

import logging
from typing import Dict, Optional, Tuple

from thonny.languages import tr

from thonny.plugins.aia import local_models as lm

logger = logging.getLogger(__name__)

POLL_MS = 500
RETRY_MS = 60 * 1000
EVENT = "AIALocalModel"

# Languages that write a decimal point; the others get a decimal comma
DECIMAL_POINT = ("en", "zh", "ja", "ko", "th", "hi", "ta", "ml", "he", "ga", "vi", "id")


def _language() -> str:
    try:
        from thonny import get_workbench

        return (get_workbench().get_option("general.language") or "en").lower()
    except Exception:
        return "en"


def gb(size: Optional[int]) -> str:
    """13 793 441 566 -> "13,8 GB" (decimal GB, like the registry)."""
    if size is None:
        return "?"
    value = size / lm.GB
    text = ("%.1f" % value) if value < 100 else ("%.0f" % value)
    if not _language().startswith(DECIMAL_POINT):
        text = text.replace(".", ",")
    return tr("%s GB") % text


def duration(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    minutes = int(round(seconds / 60))
    if minutes < 1:
        return tr("less than a minute")
    if minutes < 60:
        return tr("%d min") % minutes
    return tr("%d h %d min") % (minutes // 60, minutes % 60)


# --- the panel ---


def ready_text() -> str:
    return tr("The assistant is ready.")


def preparing_text(status: Dict) -> str:
    """The panel while the local model is being prepared."""
    if status.get("stage") == "model" and status.get("total"):
        left = duration(status.get("eta"))
        if left:
            return tr("The assistant is getting ready... %s of %s, about %s left") % (
                gb(status.get("done", 0)),
                gb(status["total"]),
                left,
            )
        return tr("The assistant is getting ready... %s of %s") % (
            gb(status.get("done", 0)),
            gb(status["total"]),
        )
    return tr("The assistant is getting ready...")


def resumed_text() -> str:
    return tr("The download continues where it stopped.")


def problem(status: Dict) -> Optional[Tuple[str, bool]]:
    """Why the local model cannot answer now, and whether Tools > Options helps; None when
    it can (or when it is not this application's local model)."""
    phase = status.get("phase")
    if phase in (lm.SYSTEM, lm.READY, lm.OFF, None):
        return None
    if phase in (lm.CHECKING, lm.STARTING):
        return tr("The assistant is getting ready..."), False
    if phase == lm.DOWNLOADING:
        return preparing_text(status), False
    if phase == lm.CONSENT:
        return tr("The local model is not downloaded yet."), True
    if phase == lm.WEAK:
        return weak_text(status.get("ram")), True
    if phase == lm.PAUSED:
        return paused_text(status), True
    return failed_text(status), status.get("reason") != lm.SIGNATURE


def weak_text(ram: Optional[int]) -> str:
    return tr(
        "This computer has %s of memory, the local model needs at least 16 GB. Use an API key "
        "instead."
    ) % gb_ram(ram)


def gb_ram(ram: Optional[int]) -> str:
    """Installed memory in whole GB as the computer's owner knows it (16 GB, not 17,2 GB)."""
    if ram is None:
        return "?"
    return tr("%s GB") % ("%d" % round(ram / lm.GIB))


def paused_text(status: Dict) -> str:
    reason = status.get("reason")
    if reason == lm.NO_SPACE:
        return tr(
            "There is not enough space on the disk for the local model. Free some space, then "
            "continue in Tools > Options > AI Assistant."
        )
    if reason == lm.NETWORK:
        return tr(
            "Preparing the assistant stopped: there is no connection to the internet. It will "
            "try again in a minute."
        )
    if status.get("total"):
        return tr("Preparing the assistant is paused (%s of %s).") % (
            gb(status.get("done", 0)),
            gb(status["total"]),
        )
    return tr("Preparing the assistant is paused.")


def failed_text(status: Dict) -> str:
    reason = status.get("reason")
    if reason == lm.SIGNATURE:
        return tr(
            "The downloaded files of the local model are not correctly signed, so they were "
            "not started."
        )
    if reason == lm.SERVER:
        return tr(
            "The local model cannot be started on this computer. Use an API key instead."
        )
    return tr("Preparing the local model failed: %s") % (status.get("error") or "?")


# --- the consent note (welcome dialog, Tools > Options) ---


def consent_text(info: Dict) -> str:
    """What OK (or Prepare) agrees to: the size, the free space, slow on 16-23 GB."""
    model = info.get("model", "")
    if info.get("ram_class") == "weak":
        return weak_text(info.get("ram"))
    if info.get("size") is None:
        text = tr(
            "The model %s will be downloaded; its size cannot be found out now. The download "
            "runs in the background, you can use Thonny meanwhile."
        ) % model
    elif not info.get("enough_disk", True):
        return tr(
            "The model %s needs %s on the disk, but only %s is free. Free some space first."
        ) % (model, gb((info.get("need") or 0) + lm.DISK_RESERVE), gb(info.get("free")))
    else:
        text = tr(
            "%s will be downloaded, you have %s free on the disk. The download runs in the "
            "background, you can use Thonny meanwhile."
        ) % (gb(info.get("need") or info["size"]), gb(info.get("free")))
    if info.get("ram_class") == "slow":
        text += " " + tr("This computer has %s of memory: the local model will be slow.") % gb_ram(
            info.get("ram")
        )
    return text


def can_prepare(info: Dict) -> bool:
    return info.get("ram_class") != "weak" and info.get("enough_disk", True)


# --- wiring ---


def chosen_model(wb, core) -> Optional[str]:
    """The local model the manager should prepare and run, None when it has nothing to do.
    UI thread."""
    if not wb.get_option("aia.enabled") or wb.get_option("aia.provider") != "ollama":
        return None
    if (wb.get_option("aia.ollama_host") or "").strip():
        return None  # an Ollama set by hand: used as it is, as before
    provider = core.providers.get("ollama")
    return core.get_current_model(provider) if provider is not None else None


def handles(wb) -> bool:
    """Whether the application's local model decides when the provider can answer."""
    return wb.get_option("aia.provider") == "ollama" and not (
        wb.get_option("aia.ollama_host") or ""
    ).strip()


class Wiring:
    def __init__(self, wb, core, manager: Optional[lm.LocalModels] = None):
        self._wb = wb
        self._core = core
        self.manager = manager or lm.get_manager()
        self._seq = None
        self._phase = None
        self._configure_timer = None
        self._retry_timer = None

    def install(self, check_updates: bool = True):
        wb = self._wb
        for option in ("aia.provider", "aia.enabled", "aia.ollama_model", "aia.ollama_host"):
            try:
                wb.get_variable(option).trace_add("write", self._on_settings_changed)
            except Exception:
                logger.debug("Could not trace %s", option, exc_info=True)
        wb.bind("WorkbenchClose", self._on_close, True)
        self.manager.configure(chosen_model(wb, self._core))
        self.manager.start(check_updates=check_updates)
        self._poll()

    def _on_settings_changed(self, *_args):
        # several options change at once (Options dialog OK): react once
        if self._configure_timer is not None:
            self._wb.after_cancel(self._configure_timer)
        self._configure_timer = self._wb.after(100, self._configure)

    def _configure(self):
        self._configure_timer = None
        self.manager.configure(chosen_model(self._wb, self._core))

    def _poll(self):
        status = self.manager.status()
        if status.get("seq") != self._seq:
            self._seq = status.get("seq")
            phase = status.get("phase")
            if phase != self._phase:
                self._phase = phase
                self._schedule_retry(status)
            try:
                self._wb.event_generate(EVENT, status=status)
            except Exception:
                logger.debug("Could not announce the local model's status", exc_info=True)
        self._wb.after(POLL_MS, self._poll)

    def _schedule_retry(self, status):
        if self._retry_timer is not None:
            self._wb.after_cancel(self._retry_timer)
            self._retry_timer = None
        if status.get("phase") == lm.PAUSED and status.get("reason") == lm.NETWORK:
            self._retry_timer = self._wb.after(RETRY_MS, self._retry)

    def _retry(self):
        self._retry_timer = None
        self.manager.refresh()

    def _on_close(self, event=None):
        self.manager.shutdown()


_wiring: Optional[Wiring] = None


def install(wb, core, check_updates: bool = True) -> Wiring:
    global _wiring
    _wiring = Wiring(wb, core)
    _wiring.install(check_updates)
    return _wiring
