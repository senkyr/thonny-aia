# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""The local model prepared by Thonny AIA (cortex-zamestnani#120), against a fake Ollama and
a fake release server on 127.0.0.1. Nothing here downloads from the internet."""

import io
import json
import os
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from thonny.plugins.aia import local_models as lm

MODEL = "tiny:1b"
DIGEST = "sha256:" + "a" * 64
CONFIG = "sha256:" + "b" * 64
LAYERS = {DIGEST: 4000, CONFIG: 100}


# --- a fake Ollama: /api/version, /api/tags, /api/pull (streamed, slow), /api/delete ---


class FakeOllama:
    def __init__(self, port, models_dir, version="1.0", step_s=0.02, steps=20):
        self.version = version
        self.models_dir = models_dir
        self.step_s = step_s
        self.steps = steps
        self.pulls = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _json(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                if self.path == "/api/version":
                    self._json({"version": fake.version})
                elif self.path == "/api/tags":
                    models = [{"model": m, "size": 4100} for m in fake.models()]
                    self._json({"models": models})
                else:
                    self._json({"error": "not found"}, 404)

            def do_DELETE(self):
                model = self._body()["model"]
                path = lm.manifest_path(model, os.path.dirname(fake.models_dir))
                if os.path.exists(path):
                    os.remove(path)
                self._json({})

            def do_POST(self):
                if self.path != "/api/pull":
                    return self._json({"error": "not found"}, 404)
                model = lm.full_name(self._body()["model"])
                fake.pulls.append(model)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for i in range(1, fake.steps + 1):
                        line = {
                            "status": "pulling",
                            "digest": DIGEST,
                            "total": LAYERS[DIGEST],
                            "completed": LAYERS[DIGEST] * i // fake.steps,
                        }
                        self.wfile.write(json.dumps(line).encode() + b"\n")
                        self.wfile.flush()
                        time.sleep(fake.step_s)
                    path = lm.manifest_path(model, os.path.dirname(fake.models_dir))
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w") as fp:
                        fp.write("{}")
                    self.wfile.write(b'{"status":"success"}\n')
                except OSError:
                    pass  # the client went away: the download stops

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def models(self):
        root = os.path.join(self.models_dir, "manifests")
        found = []
        for folder, _dirs, files in os.walk(root):
            for tag in files:
                name = os.path.basename(folder)
                found.append("%s:%s" % (name, tag))
        return found

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class FakeProcess:
    def __init__(self, server=None, returncode=None):
        self.server = server
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


class Env:
    """Fakes for one LocalModels: versions of Ollama 'downloaded', servers started."""

    def __init__(self, tmp_path):
        self.base = str(tmp_path / "local-models")
        self.port = lm.free_port()
        self.latest = "1.0"
        self.broken = set()  # versions whose server does not start
        self.unsigned = set()
        self.started = []
        self.servers = []
        self.layers_calls = 0
        self.step_s = 0.002  # per step of a pull (20 steps)

    def download(self, version, target, progress, stop):
        if version in self.unsigned:
            raise lm.SignatureError(["ollama.exe"])
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "ollama.exe"), "w") as fp:
            fp.write(version)
        progress(10, 10)

    def start(self, folder, port, models, log=None):
        version = os.path.basename(folder)[len("ollama-") :]
        self.started.append((version, port))
        if version in self.broken:
            return FakeProcess(returncode=1)
        server = FakeOllama(port, models, version, step_s=self.step_s)
        self.servers.append(server)
        return FakeProcess(server)

    def layers(self, model):
        self.layers_calls += 1
        return dict(LAYERS)

    def manager(self, ram=32 * lm.GIB, system_host="http://127.0.0.1:1"):
        return lm.LocalModels(
            base=self.base,
            port=self.port,
            system_host=system_host,
            latest=lambda: self.latest,
            download=self.download,
            layers=self.layers,
            ram=lambda: ram,
            system_installed=lambda: False,
            system_wait=0,
            start=self.start,
        )

    def close(self):
        for server in self.servers:
            try:
                server.stop()
            except Exception:
                pass


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def wait_phase(manager, phases, timeout=10.0):
    phases = {phases} if isinstance(phases, str) else set(phases)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status()
        if status["phase"] in phases and manager.wait_idle(0.01):
            return status
        time.sleep(0.01)
    raise AssertionError("still %s, waiting for %s" % (manager.status(), phases))


# --- pure helpers ---


def test_version_tuple_and_names():
    assert lm.version_tuple("0.40.16") > lm.version_tuple("0.40.3")
    assert lm.version_tuple("v0.41.0-rc1") == (0, 41, 0)
    assert lm.full_name("gemma3") == "gemma3:latest"
    assert lm.manifest_url("gpt-oss:20b").endswith("/v2/library/gpt-oss/manifests/20b")
    assert lm.manifest_url("user/m:q4").endswith("/v2/user/m/manifests/q4")


def test_ram_class_thresholds():
    assert lm.ram_class(8 * lm.GIB) == "weak"
    assert lm.ram_class(16 * lm.GIB) == "slow"  # installed 16 GB
    assert lm.ram_class(int(15.9 * lm.GIB)) == "slow"  # firmware keeps a little
    assert lm.ram_class(24 * lm.GIB) == "fine"
    assert lm.ram_class(None) == "fine"


def test_downloaded_bytes_reads_part_files(tmp_path):
    base = str(tmp_path)
    blob = lm.blob_path(DIGEST, base)
    os.makedirs(os.path.dirname(blob))
    open(blob + "-partial", "wb").close()  # allocated at full size by Ollama
    for n, done in enumerate((700, 300)):
        with open("%s-partial-%d" % (blob, n), "w") as fp:
            json.dump({"N": n, "Completed": done}, fp)
    open(lm.blob_path(CONFIG, base), "wb").close()
    assert lm.downloaded_bytes(LAYERS, base) == 1000 + 100
    assert lm.present_bytes(LAYERS, base) == 4100  # what the disk check counts


def test_model_info_counts_space(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "free_disk", lambda path: 3 * lm.GB)
    info = lm.model_info(MODEL, str(tmp_path), layers=lambda m: {DIGEST: 2 * lm.GB}, ram=lambda: 16 * lm.GIB)
    assert info["size"] == 2 * lm.GB and info["need"] == 2 * lm.GB
    assert not info["enough_disk"]  # 2 GB + 2 GB reserve > 3 GB
    assert info["ram_class"] == "slow"


def test_consent_written_before_the_manager_runs(tmp_path):
    lm.give_consent("gemma3", str(tmp_path))
    assert lm.read_state(str(tmp_path))["wanted"] == "gemma3:latest"


# --- the manager ---


def test_needs_consent_then_prepares(env):
    m = env.manager()
    m.start(check_updates=False)
    m.configure(MODEL)
    status = wait_phase(m, lm.CONSENT)
    assert status["size"] == 4100 and status["enough_disk"]
    assert m.host() is None and not env.started  # nothing downloaded or started yet
    m.prepare(MODEL)
    status = wait_phase(m, lm.READY)
    assert m.host() == lm.own_host(env.port)
    assert status["own_models"] == {MODEL: 4100}
    assert lm.read_state(env.base)["current"] == "1.0"
    m.shutdown()


def test_a_ready_model_starts_without_consent(env):
    m = env.manager()
    m.start(check_updates=False)
    m.prepare(MODEL)
    m.configure(MODEL)
    wait_phase(m, lm.READY)
    m.shutdown()
    lm.write_state(dict(lm.read_state(env.base), wanted=None), env.base)
    m2 = env.manager()
    m2.start(check_updates=False)
    m2.configure(MODEL)
    wait_phase(m2, lm.READY)
    m2.shutdown()


def test_system_ollama_with_the_model_takes_precedence(env, tmp_path):
    system = FakeOllama(lm.free_port(), str(tmp_path / "system" / "models"))
    try:
        path = lm.manifest_path(MODEL, str(tmp_path / "system"))
        os.makedirs(os.path.dirname(path))
        open(path, "w").close()
        host = "http://127.0.0.1:%d" % system.server.server_address[1]
        m = env.manager(system_host=host)
        m.start(check_updates=False)
        m.configure(MODEL)
        wait_phase(m, lm.SYSTEM)
        assert m.host() == host and not env.started
        m.shutdown()
    finally:
        system.stop()


def test_weak_computer_is_not_offered(env):
    m = env.manager(ram=8 * lm.GIB)
    m.start(check_updates=False)
    m.configure(MODEL)
    wait_phase(m, lm.WEAK)
    m.shutdown()


def test_off_without_the_local_model(env):
    m = env.manager()
    m.start(check_updates=False)
    m.configure(None)
    wait_phase(m, lm.OFF)
    m.shutdown()


def test_pause_and_resume(env):
    env.step_s = 0.2  # slow enough to pause in the middle
    m = env.manager()
    m.start(check_updates=False)
    m.prepare(MODEL)
    m.configure(MODEL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (
        m.status()["phase"] == lm.DOWNLOADING and m.status().get("stage") == "model"
    ):
        time.sleep(0.005)
    m.pause()
    wait_phase(m, lm.PAUSED)
    assert m.status()["reason"] == lm.USER
    assert lm.read_state(env.base)["paused"]
    m.refresh()
    assert wait_phase(m, lm.PAUSED)["reason"] == lm.USER  # stays paused, also after a restart
    for server in env.servers:
        server.step_s = 0.001
    m.resume()
    wait_phase(m, lm.READY, timeout=15)
    m.shutdown()


def test_cancel_deletes_partial_files(env):
    m = env.manager()
    m.start(check_updates=False)
    blob = lm.blob_path(DIGEST, env.base)
    os.makedirs(os.path.dirname(blob))
    for name in (blob + "-partial", blob + "-partial-0", lm.blob_path("sha256:" + "c" * 64, env.base) + "-partial"):
        open(name, "w").close()
    lm.give_consent(MODEL, env.base)
    lm.write_state(dict(lm.read_state(env.base), paused=True), env.base)
    m.configure(MODEL)
    wait_phase(m, lm.PAUSED)
    m.cancel(MODEL)
    wait_phase(m, lm.CONSENT)
    left = sorted(os.listdir(os.path.dirname(blob)))
    assert left == [os.path.basename(lm.blob_path("sha256:" + "c" * 64, env.base)) + "-partial"]
    assert lm.read_state(env.base)["wanted"] is None
    m.shutdown()


def test_remove_a_model(env):
    m = env.manager()
    m.start(check_updates=False)
    m.prepare(MODEL)
    m.configure(MODEL)
    wait_phase(m, lm.READY)
    m.remove(MODEL)
    status = wait_phase(m, lm.CONSENT)
    assert status["own_models"] == {}
    assert not lm.has_own_model(MODEL, env.base)
    m.shutdown()


def test_bad_signature_runs_nothing(env):
    env.unsigned.add("1.0")
    m = env.manager()
    m.start(check_updates=False)
    m.prepare(MODEL)
    m.configure(MODEL)
    status = wait_phase(m, lm.FAILED)
    assert status["reason"] == lm.SIGNATURE and not env.started
    m.shutdown()


def test_server_that_does_not_start(env):
    env.broken.add("1.0")
    m = env.manager()
    m.start(check_updates=False)
    m.prepare(MODEL)
    m.configure(MODEL)
    assert wait_phase(m, lm.FAILED)["reason"] == lm.SERVER
    m.shutdown()


def test_network_failure_pauses(env):
    def offline(model):
        raise OSError("no network")

    env.layers = offline
    m = env.manager()
    m.start(check_updates=False)
    m.prepare(MODEL)
    m.configure(MODEL)
    assert wait_phase(m, lm.PAUSED)["reason"] == lm.NETWORK
    m.shutdown()


def test_not_enough_space_pauses(env, monkeypatch):
    monkeypatch.setattr(lm, "free_disk", lambda path: 1 * lm.GB)
    m = env.manager()
    m.start(check_updates=False)
    m.prepare(MODEL)
    m.configure(MODEL)
    assert wait_phase(m, lm.PAUSED)["reason"] == lm.NO_SPACE
    m.shutdown()


# --- updates of Ollama ---


def prepared(env):
    m = env.manager()
    m.start(check_updates=False)
    m.prepare(MODEL)
    m.configure(MODEL)
    wait_phase(m, lm.READY)
    m.shutdown()


def test_update_is_used_from_the_next_start(env):
    prepared(env)
    env.latest = "1.1"
    m = env.manager()
    m.check_update()
    state = lm.read_state(env.base)
    assert state["pending"] == "1.1" and state["current"] == "1.0"
    assert env.started[-1][0] == "1.1" and env.started[-1][1] != env.port  # tried on a free port
    m2 = env.manager()
    m2.start(check_updates=False)
    m2.configure(MODEL)
    wait_phase(m2, lm.READY)
    m2.shutdown()
    state = lm.read_state(env.base)
    assert (state["current"], state["backup"], state["pending"]) == ("1.1", "1.0", None)
    assert env.servers[-1].version == "1.1"
    # the next update leaves only the running version and the one before it
    env.latest = "1.2"
    env.manager().check_update()
    m3 = env.manager()
    m3.start(check_updates=False)
    m3.configure(MODEL)
    wait_phase(m3, lm.READY)
    m3.shutdown()
    folders = sorted(n for n in os.listdir(env.base) if n.startswith("ollama-"))
    assert folders == ["ollama-1.1", "ollama-1.2"]


def test_update_that_fails_the_trial_is_not_used(env):
    prepared(env)
    env.latest = "1.1"
    env.broken.add("1.1")
    env.manager().check_update()
    state = lm.read_state(env.base)
    assert state["pending"] is None and "1.1" in state["bad"]
    assert not os.path.exists(lm.ollama_dir("1.1", env.base))
    env.manager().check_update()  # not tried again
    assert [v for v, _ in env.started].count("1.1") == 1


def test_update_that_fails_at_the_next_start_falls_back(env):
    prepared(env)
    env.latest = "1.1"
    env.manager().check_update()
    env.broken.add("1.1")  # it passed the trial, but does not start now
    m = env.manager()
    m.start(check_updates=False)
    m.configure(MODEL)
    wait_phase(m, lm.READY)
    m.shutdown()
    state = lm.read_state(env.base)
    assert state["current"] == "1.0" and "1.1" in state["bad"] and state["pending"] is None
    assert env.servers[-1].version == "1.0"


def test_no_update_check_without_own_ollama(env):
    env.latest = "9.9"
    m = env.manager()
    m.check_update()
    assert not os.path.exists(env.base) or not os.listdir(env.base)


# --- the CPU part of the release zip, by HTTP Range ---


def make_zip():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lib/ollama/ggml-base.dll", b"base" * 1000)
        zf.writestr("lib/ollama/cuda_v12/huge.dll", os.urandom(300_000))
        zf.writestr("lib/ollama/ggml-cpu-x64.dll", b"cpu" * 1000)
        zf.writestr("lib/ollama/vulkan/ggml-vulkan.dll", b"vk" * 1000)
        zf.writestr("ollama.exe", b"exe" * 5000)
    return data.getvalue()


@pytest.fixture
def release_server():
    payload = make_zip()
    ranges = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            spec = self.headers.get("Range", "")
            start, end = spec[len("bytes=") :].split("-")
            start, end = int(start), min(int(end), len(payload) - 1)
            ranges.append((start, end))
            body = payload[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, len(payload)))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/ollama-windows-amd64.zip" % server.server_address[1], ranges
    server.shutdown()
    server.server_close()


def test_download_takes_only_the_cpu_part(tmp_path, release_server, monkeypatch):
    url, ranges = release_server
    monkeypatch.setattr(lm, "_opener", lambda local: lm.urllib.request.build_opener(lm.urllib.request.ProxyHandler({})))
    monkeypatch.setattr(lm, "SPAN_GAP", 1000)  # the real 1 MB would merge over the fake cuda
    target = str(tmp_path / "ollama-1.0")
    progress = []
    lm.download_ollama("1.0", target, lambda d, t: progress.append((d, t)), url=url, signer_of=lambda p: lm.SIGNER)
    files = sorted(
        os.path.relpath(os.path.join(r, f), target).replace(os.sep, "/") for r, _d, fs in os.walk(target) for f in fs
    )
    assert files == ["lib/ollama/ggml-base.dll", "lib/ollama/ggml-cpu-x64.dll", "ollama.exe"]
    assert open(os.path.join(target, "ollama.exe"), "rb").read() == b"exe" * 5000
    assert progress[-1][0] == progress[-1][1] > 0
    assert sum(e - s + 1 for s, e in ranges) < 200_000  # the 300 kB of cuda was not downloaded
    assert not os.path.exists(target + ".partial")


def test_download_with_a_bad_signature_leaves_nothing(tmp_path, release_server, monkeypatch):
    url, _ranges = release_server
    monkeypatch.setattr(lm, "_opener", lambda local: lm.urllib.request.build_opener(lm.urllib.request.ProxyHandler({})))
    target = str(tmp_path / "ollama-1.0")
    with pytest.raises(lm.SignatureError):
        lm.download_ollama(
            "1.0", target, url=url, signer_of=lambda p: None if p.endswith("ggml-base.dll") else lm.SIGNER
        )
    assert not os.path.exists(target) and not os.path.exists(target + ".partial")
