# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""Keep the local model (Ollama) loaded while Thonny runs.

Loading gpt-oss:20b takes 35-55 s on CPU and Ollama unloads an idle model after
5 minutes, so the first error explanation of a lesson would wait almost a minute
just for the load. Instead the model is loaded at startup and its keep_alive is
renewed periodically; every chat request renews it too (``KEEP_ALIVE`` in the
provider). A refresh instead of ``keep_alive=-1`` means a crashed Thonny frees
the memory (about 13 GB) within ``KEEP_ALIVE`` - a clean exit frees it at once.

A load that Ollama did not answer (not started yet - its tray app starts with Windows and
may come up after Thonny) is retried after ``RETRY_MS``, not after ``REFRESH_MS``:
otherwise the first error of a lesson waited for the whole load anyway. A load that Ollama
refused (the model is not pulled - 404, it does not fit into memory - 500) is not retried
(Jakub 2026-10-17, cortex-zamestnani#80): the next attempt comes with another choice in
Tools > Options or after the local model answers a chat request (``model_answered``).
Until then the chat request tells the student why it fails.

Threading: the timer and option reads run on the UI thread, the HTTP requests in
a daemon thread with a frozen provider.
"""

import logging
import threading

from thonny.plugins.aia.providers.ollama_provider import KEEP_ALIVE

logger = logging.getLogger(__name__)

# Renew well before KEEP_ALIVE (10 min) runs out
REFRESH_MS = 4 * 60 * 1000
FIRST_LOAD_DELAY_MS = 1000
RETRY_MS = 15 * 1000


_keeper = None  # the installed keeper, for is_loading()


def is_loading() -> bool:
    """Whether the local model is on its way into memory (the empty panel says so).

    Not during the periodic renewal of a model that is loaded already.
    """
    return _keeper is not None and _keeper._loading and not _keeper._ready


def model_answered():
    """The local model answered a chat request (UI thread): a keeper that gave up loads again.

    The student may have freed memory or pulled the model since the keeper gave up.
    """
    if _keeper is not None and _keeper._gave_up and not _keeper._loading:
        _keeper._tick()


def worth_retrying(exc: BaseException) -> bool:
    """Ollama did not answer at all (not running yet, timeout): its status code is unknown.

    An error Ollama answered with (ollama.ResponseError: 404 model not pulled, 500 model does
    not fit into memory) comes again on every attempt.
    """
    return getattr(exc, "status_code", None) is None


class LocalModelKeeper:
    def __init__(self, wb, core):
        self._wb = wb
        self._core = core
        self._loaded = None  # (frozen provider, model) that we asked Ollama to keep
        self._timer = None
        # State of the last load, written by its worker thread, read by the timer
        self._loading = False
        self._failed = False
        self._gave_up = False  # the last load failed in a way that retrying cannot fix
        self._ready = False  # the wanted model has been loaded (renewals do not reset it)

    def install(self):
        global _keeper
        _keeper = self
        try:
            # the model chosen in Tools > Options too: the keeper loads that one instead
            for option in ("aia.provider", "aia.enabled", "aia.ollama_model"):
                self._wb.get_variable(option).trace_add("write", self._on_settings_changed)
        except Exception:
            logger.debug("Could not trace AIA options", exc_info=True)
        self._wb.bind("WorkbenchClose", self._on_close, True)
        # the prepared local model becomes usable (or stops being): load it then
        self._local_ready = None
        self._wb.bind("AIALocalModel", self._on_local_model, True)
        self._schedule(FIRST_LOAD_DELAY_MS)

    def _on_local_model(self, event=None):
        from thonny.plugins.aia.local_models import get_manager

        ready = (get_manager().ready(), get_manager().host())
        if ready != self._local_ready:
            self._local_ready = ready
            self._on_settings_changed()

    def wanted(self):
        """(frozen provider, model) to keep loaded, or None. UI thread only."""
        wb = self._wb
        if not wb.get_option("aia.enabled") or wb.get_option("aia.provider") != "ollama":
            return None
        provider = self._core.providers.get("ollama")
        if provider is None:
            return None
        from thonny.plugins.aia import local_ui
        from thonny.plugins.aia.local_models import get_manager

        if local_ui.handles(wb) and not get_manager().ready():
            return None  # being prepared, or not available: nothing to load yet
        return provider.frozen(), self._core.get_current_model(provider)

    def _schedule(self, delay_ms, func=None):
        if self._timer is not None:
            try:
                self._wb.after_cancel(self._timer)
            except Exception:
                pass
        self._timer = self._wb.after(delay_ms, func or self._tick)

    def _on_settings_changed(self, *_args):
        # The options may change several at once (Options dialog OK) - react once
        self._schedule(FIRST_LOAD_DELAY_MS)

    def _tick(self):
        self._timer = None
        wanted = self.wanted()
        previous = self._loaded
        if previous is not None and (wanted is None or wanted[1] != previous[1]):
            self._run(previous[0].unload, previous[1])
        if wanted is None or previous is None or wanted[1] != previous[1]:
            self._ready = False
        self._loaded = wanted
        if wanted is not None:
            self._run(wanted[0].preload, wanted[1], load=True)
            self._schedule(RETRY_MS, self._check)

    def _check(self):
        """RETRY_MS after a load started: retry a failed load, else wait for the refresh."""
        self._timer = None
        if self._loading:
            self._schedule(RETRY_MS, self._check)  # loading from disk takes ~30 s
        elif self._gave_up:
            pass  # wait for another choice or for model_answered()
        elif self._failed:
            self._tick()
        else:
            self._schedule(REFRESH_MS)

    def _run(self, func, model, load=False):
        if load:
            self._loading = True
            self._failed = False
            self._gave_up = False

        def work():
            try:
                func(model)
                if load:
                    self._ready = True
                    self._after_load(func.__self__, model)
            except Exception as e:
                # Server not running, model not pulled or too big - the chat request reports it
                logger.info("AIA: local model %s: %s failed", model, func.__name__, exc_info=True)
                if load:
                    self._load_failed(e)
            finally:
                if load:
                    self._loading = False

        threading.Thread(target=work, daemon=True).start()

    def _load_failed(self, exc):
        """Worker thread: remember whether the next check retries the load."""
        self._gave_up = not worth_retrying(exc)
        self._failed = True

    def _after_load(self, provider, model):
        """Worker thread: unload a model that is no longer wanted once its load is done.

        Switching to another choice while the model loads sends the unload request during the
        load; Ollama finishes the load after it and keeps the model for KEEP_ALIVE.
        """
        wanted = self._loaded  # set on the UI thread, read only
        if wanted is None or wanted[1] != model:
            provider.unload(model)

    def _on_close(self, event=None):
        if self._loaded is None:
            return
        provider, model = self._loaded
        self._loaded = None
        try:
            provider.unload(model, timeout=2.0)  # synchronous: the process ends right after
        except Exception:
            logger.debug("Could not unload the local model", exc_info=True)
