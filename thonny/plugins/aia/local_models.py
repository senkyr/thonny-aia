# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""The local model that Thonny AIA prepares by itself (cortex-zamestnani#118, #120).

After the user's consent (OK in the welcome dialog or in Tools > Options with the local model
chosen, or "Prepare" in Tools > Options) Thonny AIA downloads the CPU part of Ollama's
``ollama-windows-amd64.zip`` (about 30 MB of 1.6 GB, by HTTP Range), checks that every .exe and
.dll is signed by Ollama Inc., runs ``ollama serve`` on its own port without a window and pulls
the chosen model through Ollama's API. Everything lives in ``%LOCALAPPDATA%\\Thonny AIA\\
local-models`` (``ollama-<version>\\``, ``models\\``, ``state.json``), so removing that folder
undoes it all. No installer, no administrator rights, nothing in the Startup folder.

An Ollama on 11434 that has the chosen model takes precedence and nothing is downloaded.
Another Ollama is never changed or deleted.

Ollama is updated at every start: the redirect of ``releases/latest`` gives the newest
version, which is downloaded in the background, checked and tried on a free port, and used
from the next start on. When it then does not come up, the previous version is used again and
the new one is remembered as bad. At most two versions stay on disk: the running one and the
last one that worked.

Threading: ``LocalModels`` does its work in one worker thread (a queue of commands) and in a
thread per download; the UI reads ``status()`` (a copy) and calls the command methods, which
only enqueue. Nothing here touches Tk.
"""

import ctypes
import io
import json
import logging
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Addresses (environment variables only for the harness) ---

OWN_PORT = int(os.environ.get("THONNY_AIA_OWN_PORT") or 11439)
SYSTEM_HOST = os.environ.get("THONNY_AIA_SYSTEM_OLLAMA") or "http://127.0.0.1:11434"
RELEASES_URL = os.environ.get("THONNY_AIA_OLLAMA_RELEASES") or "https://github.com/ollama/ollama/releases"
REGISTRY_URL = os.environ.get("THONNY_AIA_OLLAMA_REGISTRY") or "https://registry.ollama.ai"
ZIP_NAME = "ollama-windows-amd64.zip"
SIGNER = "Ollama Inc."

# --- Limits (Jakub 2026-11-12, cortex-zamestnani#118) ---

GB = 1000**3  # sizes are shown in decimal GB, like the registry's manifest
GIB = 1024**3
MIN_RAM_GIB = 16  # below: the local model is not offered
FULL_RAM_GIB = 24  # 16-23: offered with a warning that it will be slow
DISK_RESERVE = 2 * GB  # free space needed on top of the model

SERVER_START_TIMEOUT = 60.0  # the first start of a fresh ollama.exe waits for the virus scan
TRIAL_TIMEOUT = 30.0
HTTP_TIMEOUT = 30.0
LOCAL_TIMEOUT = 2.0
SPAN_GAP = 1024 * 1024  # zip members closer than this are downloaded in one request
CHUNK = 256 * 1024

# Phases of status()["phase"]
OFF = "off"  # the local model is not chosen (or another Ollama host is set by hand)
CHECKING = "checking"
SYSTEM = "system"  # the Ollama on 11434 has the model: nothing to prepare
WEAK = "weak"  # too little memory to prepare it here
CONSENT = "consent"  # the model is missing, waiting for the user's consent
DOWNLOADING = "downloading"
PAUSED = "paused"
STARTING = "starting"
READY = "ready"  # the own Ollama runs with the model
FAILED = "failed"

# Reasons (status()["reason"]) for PAUSED and FAILED
USER = "user"  # paused by the user
NETWORK = "network"
NO_SPACE = "no_space"
SIGNATURE = "signature"
SERVER = "server"  # the own Ollama did not start
OTHER = "other"


def base_dir() -> str:
    custom = os.environ.get("THONNY_AIA_LOCAL_MODELS")
    if custom:
        return custom
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "Thonny AIA", "local-models")


def own_host(port: int = OWN_PORT) -> str:
    return "http://127.0.0.1:%d" % port


def version_tuple(version: str) -> Tuple[int, ...]:
    parts = []
    for part in re.split(r"[.\-+]", version.lstrip("v")):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


def full_name(model: str) -> str:
    """Ollama lists "gemma3" as "gemma3:latest"."""
    return model if ":" in model else model + ":latest"


# --- Network ---


def _opener(local: bool):
    """Like Ollama: a proxy only from HTTPS_PROXY (Go does not read the Windows registry,
    and a school proxy there answered 127.0.0.1 with 503); none for this computer."""
    proxies = {} if local else urllib.request.getproxies_environment()
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def latest_version(timeout: float = HTTP_TIMEOUT) -> str:
    """The newest Ollama release, from the redirect of releases/latest ("0.40.16").

    Not GitHub's API: without signing in it allows 60 requests an hour per IP address, which a
    class behind one school NAT would use up.
    """
    proxies = urllib.request.getproxies_environment()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), _NoRedirect)
    request = urllib.request.Request(RELEASES_URL + "/latest", method="HEAD")
    try:
        with opener.open(request, timeout=timeout) as response:
            location = response.headers.get("Location") or response.geturl()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        location = e.headers.get("Location") or ""
    match = re.search(r"/tag/v?([0-9][^/?#]*)$", location)
    if not match:
        raise OSError("Unexpected address of the latest Ollama release: %r" % location)
    return match.group(1)


def zip_url(version: str) -> str:
    return "%s/download/v%s/%s" % (RELEASES_URL, version, ZIP_NAME)


class RangeFile(io.RawIOBase):
    """A remote file read by HTTP Range requests, for zipfile; prefetched spans are served
    from memory."""

    def __init__(self, url: str, timeout: float = HTTP_TIMEOUT):
        self.url = url
        self.timeout = timeout
        self.pos = 0
        self.requests = 0
        self._spans: List[Tuple[int, bytes]] = []
        self._opener = _opener(local=False)
        with self._open(0, 0) as response:
            content_range = response.headers.get("Content-Range") or ""
            if response.status != 206 or "/" not in content_range:
                raise OSError("The server does not support partial downloads")
            self.size = int(content_range.rsplit("/", 1)[1])

    def _open(self, start: int, end: int):
        self.requests += 1
        request = urllib.request.Request(self.url, headers={"Range": "bytes=%d-%d" % (start, end)})
        return self._opener.open(request, timeout=self.timeout)

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def readinto(self, buffer):
        n = min(len(buffer), self.size - self.pos)
        if n <= 0:
            return 0
        for start, data in self._spans:
            if start <= self.pos and self.pos + n <= start + len(data):
                offset = self.pos - start
                buffer[:n] = data[offset : offset + n]
                self.pos += n
                return n
        with self._open(self.pos, self.pos + n - 1) as response:
            data = response.read()
        buffer[: len(data)] = data
        self.pos += len(data)
        return len(data)

    def prefetch(self, start: int, end: int, progress: Callable[[int], None], stop: threading.Event):
        """Download bytes start..end-1 into memory, calling progress(bytes) per chunk."""
        chunks = []
        with self._open(start, end - 1) as response:
            while True:
                if stop.is_set():
                    raise Interrupted()
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
                progress(len(chunk))
        data = b"".join(chunks)
        if len(data) != end - start:
            raise OSError("Incomplete download")
        self._spans.append((start, data))


class Interrupted(Exception):
    """The download was paused or cancelled."""


def cpu_members(zf: zipfile.ZipFile) -> List[zipfile.ZipInfo]:
    """ollama.exe and the files right in lib/ollama/: the CPU part (cuda_v12, cuda_v13 and
    vulkan are subfolders). Without vulkan Ollama runs only on the processor, which also avoids
    the GT 710 crash (cortex-zamestnani#64)."""
    members = []
    for info in zf.infolist():
        name = info.filename
        if info.is_dir():
            continue
        if name == "ollama.exe" or (
            name.startswith("lib/ollama/") and "/" not in name[len("lib/ollama/") :]
        ):
            members.append(info)
    return members


def member_spans(zf: zipfile.ZipFile, members: List[zipfile.ZipInfo]) -> List[Tuple[int, int]]:
    """Byte ranges (start, end) of the zip holding the members with their local headers,
    neighbours closer than SPAN_GAP merged into one range."""
    offsets = sorted({i.header_offset for i in zf.infolist()})
    central = zf.start_dir
    ranges = []
    for info in members:
        later = [o for o in offsets if o > info.header_offset]
        end = min(later[0] if later else central, central)
        ranges.append((info.header_offset, end))
    ranges.sort()
    merged: List[List[int]] = []
    for start, end in ranges:
        if merged and start - merged[-1][1] <= SPAN_GAP:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


# --- Signatures (WinVerifyTrust, not PowerShell: a student's account may not run it) ---


def signer_of(path: str) -> Optional[str]:
    """Name of the valid Authenticode signer of the file ("Ollama Inc."), None when unsigned
    or invalid. Windows only."""
    from ctypes import wintypes as W

    class GUID(ctypes.Structure):
        _fields_ = [
            ("d1", ctypes.c_uint32),
            ("d2", ctypes.c_uint16),
            ("d3", ctypes.c_uint16),
            ("d4", ctypes.c_ubyte * 8),
        ]

    class FILE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbStruct", W.DWORD),
            ("pcwszFilePath", W.LPCWSTR),
            ("hFile", W.HANDLE),
            ("pgKnownSubject", ctypes.c_void_p),
        ]

    class TRUST_DATA(ctypes.Structure):
        _fields_ = [
            ("cbStruct", W.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", W.DWORD),
            ("fdwRevocationChecks", W.DWORD),
            ("dwUnionChoice", W.DWORD),
            ("pFile", ctypes.POINTER(FILE_INFO)),
            ("dwStateAction", W.DWORD),
            ("hWVTStateData", W.HANDLE),
            ("pwszURLReference", W.LPCWSTR),
            ("dwProvFlags", W.DWORD),
            ("dwUIContext", W.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        ]

    class PROVIDER_CERT(ctypes.Structure):
        _fields_ = [("cbStruct", W.DWORD), ("pCert", ctypes.c_void_p)]

    # WINTRUST_ACTION_GENERIC_VERIFY_V2
    action = GUID(
        0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE)
    )
    wintrust = ctypes.WinDLL("wintrust")
    crypt32 = ctypes.WinDLL("crypt32")
    wintrust.WinVerifyTrust.argtypes = [W.HWND, ctypes.POINTER(GUID), ctypes.c_void_p]
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    wintrust.WTHelperProvDataFromStateData.argtypes = [W.HANDLE]
    wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, W.DWORD, W.BOOL, W.DWORD]
    wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvCertFromChain.argtypes = [ctypes.c_void_p, W.DWORD]
    wintrust.WTHelperGetProvCertFromChain.restype = ctypes.c_void_p
    crypt32.CertGetNameStringW.argtypes = [
        ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.c_void_p, W.LPWSTR, W.DWORD
    ]
    crypt32.CertGetNameStringW.restype = W.DWORD

    file_info = FILE_INFO(ctypes.sizeof(FILE_INFO), path, None, None)
    data = TRUST_DATA()
    data.cbStruct = ctypes.sizeof(TRUST_DATA)
    data.dwUIChoice = 2  # WTD_UI_NONE
    data.fdwRevocationChecks = 0  # WTD_REVOKE_NONE: no network stalls, the files come over HTTPS
    data.dwUnionChoice = 1  # WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = 1  # WTD_STATEACTION_VERIFY
    data.dwProvFlags = 0x10 | 0x1000  # REVOCATION_CHECK_NONE | CACHE_ONLY_URL_RETRIEVAL
    result = wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
    try:
        if result != 0:
            return None
        provider = wintrust.WTHelperProvDataFromStateData(data.hWVTStateData)
        signer = wintrust.WTHelperGetProvSignerFromChain(provider, 0, False, 0)
        cert = wintrust.WTHelperGetProvCertFromChain(signer, 0) if signer else None
        if not cert:
            return None
        context = ctypes.cast(cert, ctypes.POINTER(PROVIDER_CERT)).contents.pCert
        name = ctypes.create_unicode_buffer(256)
        crypt32.CertGetNameStringW(context, 4, 0, None, name, 256)  # SIMPLE_DISPLAY_TYPE
        return name.value
    finally:
        data.dwStateAction = 2  # WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))


def check_signatures(folder: str, signer_of=signer_of) -> List[str]:
    """Files (.exe, .dll) under folder not validly signed by Ollama Inc. (empty = all fine)."""
    bad = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if name.lower().endswith((".exe", ".dll")):
                path = os.path.join(root, name)
                if signer_of(path) != SIGNER:
                    bad.append(os.path.relpath(path, folder))
    return bad


# --- This computer ---


def installed_ram() -> Optional[int]:
    """Bytes of installed memory; a machine with 16 GB reports about 15.8 GiB usable, which
    would fall below the threshold, so the installed amount and only then the usable one."""
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        kilobytes = ctypes.c_ulonglong()
        kernel32.GetPhysicallyInstalledSystemMemory.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
        kernel32.GetPhysicallyInstalledSystemMemory.restype = ctypes.c_int
        if kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kilobytes)):
            return kilobytes.value * 1024
    except Exception:
        logger.debug("GetPhysicallyInstalledSystemMemory failed", exc_info=True)
    try:
        from ctypes import wintypes as W

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", W.DWORD),
                ("dwMemoryLoad", W.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys
    except Exception:
        logger.debug("GlobalMemoryStatusEx failed", exc_info=True)
    return None


def ram_class(ram: Optional[int]) -> str:
    """'weak' (below 16 GiB: not offered), 'slow' (16-23: with a warning) or 'fine'."""
    if ram is None:
        return "fine"  # unknown: do not stand in the way
    if ram < MIN_RAM_GIB * GIB - 256 * 1024**2:  # firmware may keep a little
        return "weak"
    if ram < FULL_RAM_GIB * GIB - 256 * 1024**2:
        return "slow"
    return "fine"


def free_disk(path: str) -> Optional[int]:
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


# --- Registry (model sizes) ---


def manifest_url(model: str) -> str:
    name, _, tag = full_name(model).rpartition(":")
    if "/" not in name:
        name = "library/" + name
    return "%s/v2/%s/manifests/%s" % (REGISTRY_URL, name, tag)


def model_layers(model: str, timeout: float = HTTP_TIMEOUT) -> Dict[str, int]:
    """Digest -> size of every blob of the model (layers and config), from the registry."""
    request = urllib.request.Request(
        manifest_url(model),
        headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
    )
    with _opener(local=False).open(request, timeout=timeout) as response:
        manifest = json.loads(response.read().decode("utf-8"))
    layers = {}
    for item in list(manifest.get("layers") or []) + [manifest.get("config") or {}]:
        if item.get("digest"):
            layers[item["digest"]] = int(item.get("size") or 0)
    return layers


# --- Ollama's API ---


def api(host: str, path: str, body=None, method=None, timeout: float = LOCAL_TIMEOUT):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        host + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with _opener(local=True).open(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw.strip() else {}


def port_open(host: str, timeout: float = 0.3) -> bool:
    """Whether something listens on the host's port. Windows retries a refused connection
    to this computer for about 2 s, a listening server accepts at once."""
    from urllib.parse import urlparse

    parsed = urlparse(host)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def server_version(host: str, timeout: float = LOCAL_TIMEOUT) -> Optional[str]:
    if not port_open(host):
        return None
    try:
        return api(host, "/api/version", timeout=timeout).get("version")
    except Exception:
        return None


def server_models(host: str, timeout: float = LOCAL_TIMEOUT) -> Dict[str, int]:
    """Full name -> size of the models the server has; raises when it does not answer."""
    models = {}
    for m in api(host, "/api/tags", timeout=timeout).get("models", []):
        name = m.get("model") or m.get("name")
        if name:
            models[full_name(name)] = int(m.get("size") or 0)
    return models


def has_model(host: str, model: str, timeout: float = LOCAL_TIMEOUT) -> bool:
    if not port_open(host):
        return False
    try:
        return full_name(model) in server_models(host, timeout)
    except Exception:
        return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- The own server ---

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


class _Job:
    """A Windows job that kills the server and its runners when Thonny ends, even by a crash
    (the handle closes with the process), so 13 GB of memory are not left behind."""

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            from ctypes import wintypes as W

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
            kernel32.CreateJobObjectW.restype = W.HANDLE
            kernel32.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
            kernel32.SetInformationJobObject.restype = W.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
            kernel32.AssignProcessToJobObject.restype = W.BOOL
            self._kernel32 = kernel32

            class BASIC(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", W.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", W.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", W.DWORD),
                    ("SchedulingClass", W.DWORD),
                ]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rt", "wt", "ot")]

            class EXTENDED(ctypes.Structure):
                _fields_ = [
                    ("Basic", BASIC),
                    ("Io", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            handle = kernel32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if handle and kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                self.handle = handle
        except Exception:
            logger.debug("Could not create a job for the local model", exc_info=True)

    def assign(self, process: subprocess.Popen):
        if self.handle is None:
            return
        try:
            if not self._kernel32.AssignProcessToJobObject(self.handle, int(process._handle)):
                logger.debug("AssignProcessToJobObject failed: %s", ctypes.get_last_error())
        except Exception:
            logger.debug("Could not assign the local model's server to the job", exc_info=True)


_job = None


def start_server(
    ollama_dir: str, port: int, models_dir: str, log_path: Optional[str] = None
) -> subprocess.Popen:
    """ollama serve on 127.0.0.1:port, without a window, below normal priority."""
    global _job
    exe = os.path.join(ollama_dir, "ollama.exe" if sys.platform == "win32" else "ollama")
    env = dict(os.environ)
    for name in list(env):
        if name.upper().startswith("OLLAMA_"):
            del env[name]  # settings of another Ollama on this computer do not apply here
    env["OLLAMA_HOST"] = "127.0.0.1:%d" % port
    env["OLLAMA_MODELS"] = models_dir
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL
    try:
        flags = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 0
        process = subprocess.Popen(
            [exe, "serve"],
            cwd=ollama_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    finally:
        if log_path:
            log.close()
    if _job is None:
        _job = _Job()
    _job.assign(process)
    return process


def wait_for_server(host: str, process: Optional[subprocess.Popen], timeout: float) -> Optional[str]:
    """The server's version once it answers, None when it ends or does not answer in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        version = server_version(host, timeout=1.0)
        if version:
            return version
        if process is not None and process.poll() is not None:
            return None
        time.sleep(0.25)
    return None


def stop_process(process: Optional[subprocess.Popen]):
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()  # TerminateProcess; the job ends its runners with it
        process.wait(5)
    except Exception:
        logger.debug("Could not stop the local model's server", exc_info=True)


def remove_tree(path: str, attempts: int = 20):
    """Delete a folder; Windows keeps files of an ended process locked for a moment."""
    for _ in range(attempts):
        if not os.path.exists(path):
            return True
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        time.sleep(0.25)
    return not os.path.exists(path)


def download_ollama(
    version: str,
    target: str,
    progress: Callable[[int, int], None] = lambda done, total: None,
    stop: Optional[threading.Event] = None,
    url: Optional[str] = None,
    signer_of=signer_of,
) -> None:
    """Download the CPU part of the release's zip into target (a new folder), check the
    signatures. Raises Interrupted, OSError (network) or SignatureError; leaves nothing
    behind on failure."""
    stop = stop or threading.Event()
    partial = target + ".partial"
    remove_tree(partial)
    try:
        remote = RangeFile(url or zip_url(version))
        with zipfile.ZipFile(remote) as zf:
            members = cpu_members(zf)
            if not any(m.filename == "ollama.exe" for m in members):
                raise OSError("ollama.exe is missing in the release")
            spans = member_spans(zf, members)
            total = sum(end - start for start, end in spans)
            done = [0]

            def advance(n):
                done[0] += n
                progress(done[0], total)

            progress(0, total)
            for start, end in spans:
                remote.prefetch(start, end, advance, stop)
            for info in members:
                if stop.is_set():
                    raise Interrupted()
                path = os.path.join(partial, *info.filename.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with zf.open(info) as source, open(path, "wb") as sink:
                    shutil.copyfileobj(source, sink, CHUNK)  # zipfile checks the CRC
        bad = check_signatures(partial, signer_of)
        if bad:
            raise SignatureError(bad)
        remove_tree(target)
        os.replace(partial, target)
    finally:
        remove_tree(partial)


class SignatureError(Exception):
    """A file of the downloaded Ollama is not validly signed by Ollama Inc."""


# --- State on disk ---

STATE_FILE = "state.json"
DEFAULT_STATE = {
    "current": None,  # the Ollama version in use
    "backup": None,  # the last version that worked before it
    "pending": None,  # downloaded and tried, used from the next start
    "bad": [],  # versions that did not come up: never tried again
    "wanted": None,  # the model the user agreed to download
    "paused": False,  # the user paused its download
}


def read_state(base: Optional[str] = None) -> Dict:
    state = json.loads(json.dumps(DEFAULT_STATE))
    try:
        with open(os.path.join(base or base_dir(), STATE_FILE), encoding="utf-8") as fp:
            stored = json.load(fp)
        state.update({k: v for k, v in stored.items() if k in DEFAULT_STATE})
    except (OSError, ValueError):
        pass
    return state


def write_state(state: Dict, base: Optional[str] = None):
    base = base or base_dir()
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, STATE_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as fp:
        json.dump(state, fp, indent=1)
    os.replace(path + ".tmp", path)


def give_consent(model: str, base: Optional[str] = None):
    """The user agreed to download the model (the welcome dialog, before the manager runs)."""
    state = read_state(base)
    state["wanted"] = full_name(model)
    state["paused"] = False
    write_state(state, base)


def models_dir(base: Optional[str] = None) -> str:
    return os.path.join(base or base_dir(), "models")


def ollama_dir(version: str, base: Optional[str] = None) -> str:
    return os.path.join(base or base_dir(), "ollama-" + version)


def blob_path(digest: str, base: Optional[str] = None) -> str:
    return os.path.join(models_dir(base), "blobs", digest.replace(":", "-"))


def present_bytes(layers: Dict[str, int], base: Optional[str] = None) -> int:
    """Bytes of the model's blobs already on disk: finished, or begun (Ollama allocates a
    partial download at its full size right away)."""
    present = 0
    for digest, size in layers.items():
        path = blob_path(digest, base)
        if os.path.exists(path) or os.path.exists(path + "-partial"):
            present += size
    return present


def downloaded_bytes(layers: Dict[str, int], base: Optional[str] = None) -> int:
    """Bytes of the model really downloaded: finished blobs, and of a partial one what its
    part files (``<blob>-partial-N``, JSON with "Completed") say."""
    done = 0
    for digest, size in layers.items():
        path = blob_path(digest, base)
        if os.path.exists(path):
            done += size
            continue
        folder = os.path.dirname(path)
        prefix = os.path.basename(path) + "-partial-"
        try:
            names = [n for n in os.listdir(folder) if n.startswith(prefix)]
        except OSError:
            continue
        for name in names:
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as fp:
                    done += int(json.load(fp).get("Completed") or 0)
            except (OSError, ValueError, AttributeError):
                pass
    return done


def manifest_path(model: str, base: Optional[str] = None) -> str:
    name, _, tag = full_name(model).rpartition(":")
    parts = name.split("/")
    if len(parts) == 1:
        parts = ["registry.ollama.ai", "library"] + parts
    elif len(parts) == 2:
        parts = ["registry.ollama.ai"] + parts
    return os.path.join(models_dir(base), "manifests", *parts, tag)


def has_own_model(model: str, base: Optional[str] = None) -> bool:
    """Whether the own Ollama has the model, by its manifest on disk (no server needed)."""
    return os.path.isfile(manifest_path(model, base))


def model_info(model: str, base: Optional[str] = None, layers=model_layers, ram=None) -> Dict:
    """What the consent note says: size of the model (None when the registry cannot be
    asked), bytes still to download, free disk space, memory class. Blocks (network)."""
    info = {"model": full_name(model), "size": None, "need": None}
    try:
        found = layers(model)
        info["size"] = sum(found.values())
        info["need"] = info["size"] - present_bytes(found, base)
    except Exception:
        logger.info("AIA: could not ask the registry about %s", model, exc_info=True)
    info["free"] = free_disk(base or base_dir())
    info["ram"] = (ram or installed_ram)()
    info["ram_class"] = ram_class(info["ram"])
    info["enough_disk"] = (
        info["need"] is None or info["free"] is None or info["free"] >= info["need"] + DISK_RESERVE
    )
    return info


def system_ollama_installed() -> bool:
    local = os.environ.get("LOCALAPPDATA", "")
    return bool(shutil.which("ollama")) or os.path.exists(
        os.path.join(local, "Programs", "Ollama", "ollama.exe")
    )


# --- The manager ---


class LocalModels:
    """Prepares and runs the local model; see the module docstring."""

    def __init__(
        self,
        base: Optional[str] = None,
        port: int = OWN_PORT,
        system_host: str = SYSTEM_HOST,
        latest=latest_version,
        download=download_ollama,
        layers=model_layers,
        ram=installed_ram,
        system_installed=system_ollama_installed,
        system_wait: float = 15.0,
        start=start_server,
    ):
        self.base = base or base_dir()
        self.port = port
        self.own = own_host(port)
        self.system_host = system_host
        self._latest = latest
        self._download = download
        self._layers = layers
        self._ram = ram
        self._system_installed = system_installed
        self._system_wait = system_wait
        self._start_server = start
        self._lock = threading.RLock()
        self._commands: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()  # interrupts the running download
        self._response = None  # the pull's HTTP response, closed to interrupt it
        self._process: Optional[subprocess.Popen] = None  # the own server, if we started it
        self._thread = None
        self._update_thread = None
        self._model = None  # the local model in use, None = not chosen
        self._host = None
        self._status = {"phase": OFF, "seq": 0}
        self._closed = False

    # --- UI side (never blocks) ---

    def start(self, check_updates: bool = True):
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, name="AIA local model", daemon=True)
            self._thread.start()
            if check_updates:
                self._commands.put(("update",))

    def configure(self, model: Optional[str]):
        """The local model chosen now (None: another provider, AIA off, own host set)."""
        model = full_name(model) if model else None
        if model != self._model and self._status.get("phase") == DOWNLOADING:
            self._interrupt()
        self._model = model
        self._commands.put(("ensure",))

    def prepare(self, model: str):
        """The user agreed to download the model (OK with it chosen, or Prepare)."""
        self._commands.put(("consent", full_name(model)))

    def pause(self):
        self._commands.put(("pause",))
        self._interrupt()

    def resume(self):
        self._commands.put(("resume",))

    def cancel(self, model: str):
        self._interrupt()
        self._commands.put(("cancel", full_name(model)))

    def remove(self, model: str):
        self._commands.put(("remove", full_name(model)))

    def refresh(self):
        self._commands.put(("ensure",))

    def shutdown(self):
        """Thonny is closing: interrupt a download (it continues at the next start), stop the
        own server. Blocks a moment."""
        self._closed = True
        self._interrupt()
        self._commands.put(("quit",))
        self._stop_own()

    def status(self) -> Dict:
        with self._lock:
            return dict(self._status)

    def host(self) -> Optional[str]:
        """Address of the Ollama to use for the chosen model, None while there is none."""
        return self._host

    def ready(self) -> bool:
        return self.status()["phase"] in (SYSTEM, READY)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Tests: wait until the queue is processed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._commands.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    # --- worker ---

    def _set(self, phase, **fields):
        with self._lock:
            seq = self._status.get("seq", 0) + 1
            self._status = {"phase": phase, "seq": seq, "model": self._model, **fields}

    def _update_fields(self, **fields):
        with self._lock:
            self._status.update(fields)
            self._status["seq"] = self._status.get("seq", 0) + 1

    def _interrupt(self):
        self._stop.set()
        response = self._response
        if response is not None:
            _abort(response)

    def _work(self):
        while True:
            command = self._commands.get()
            try:
                if command[0] == "quit":
                    return
                self._handle(command)
            except Exception as e:
                logger.exception("AIA: the local model's %s failed", command[0])
                self._set(FAILED, reason=OTHER, error=str(e) or type(e).__name__)
            finally:
                self._commands.task_done()

    def _handle(self, command):
        name = command[0]
        if name == "update":
            self._start_update_check()
            return
        with self._lock:
            state = read_state(self.base)
            if name == "consent":
                state["wanted"] = command[1]
                state["paused"] = False
                write_state(state, self.base)
            elif name == "pause":
                state["paused"] = True
                write_state(state, self.base)
            elif name == "resume":
                state["paused"] = False
                write_state(state, self.base)
        if name == "cancel":
            self._cancel(command[1])
        elif name == "remove":
            self._remove(command[1])
        if not self._closed and self._commands.qsize() == 0:
            self._ensure()

    def _ensure(self):
        self._stop.clear()
        model = self._model
        if model is None:
            self._host = None
            self._stop_own()  # frees nothing big (the keeper unloads the model), but tidy
            self._set(OFF)
            return
        if self._status.get("phase") not in (READY, SYSTEM) or self._status.get("model") != model:
            self._set(CHECKING)
        if self._system_has(model):
            self._host = self.system_host
            self._set(SYSTEM)
            return
        state = read_state(self.base)
        if has_own_model(model, self.base) and state["current"]:
            if not self._start_own():
                return
            if has_model(self.own, model, timeout=5.0):
                self._host = self.own
                self._set(READY, own_models=self._own_models())
                return
        self._host = None
        if state["wanted"] != model:
            info = model_info(model, self.base, self._layers, self._ram)
            info["own_models"] = self._own_models() if state["current"] else {}
            self._set(WEAK if info["ram_class"] == "weak" else CONSENT, **info)
            return
        if state["paused"]:
            self._set(PAUSED, reason=USER, **self._progress_guess(model))
            return
        self._prepare(model)

    def _system_has(self, model) -> bool:
        if has_model(self.system_host, model):
            return True
        if server_version(self.system_host) is None and self._system_installed():
            # its tray app starts with Windows and may come up after Thonny
            deadline = time.monotonic() + self._system_wait
            while time.monotonic() < deadline and not self._stop.is_set():
                if server_version(self.system_host) is not None:
                    return has_model(self.system_host, model)
                time.sleep(1.0)
        return False

    def _own_models(self) -> Dict[str, int]:
        if server_version(self.own) is None:
            return {}
        try:
            return server_models(self.own, timeout=5.0)
        except Exception:
            return {}

    def _progress_guess(self, model) -> Dict:
        try:
            layers = self._layers(model)
        except Exception:
            return {}
        return {"done": downloaded_bytes(layers, self.base), "total": sum(layers.values())}

    # --- the own server ---

    def _start_own(self) -> bool:
        """Run the own Ollama (the pending update first, falling back to the last that
        worked). False (and FAILED) when no version comes up."""
        if server_version(self.own) is not None:
            return True  # ours from another Thonny window, or left after a crash
        state = read_state(self.base)
        candidates = []
        for version in (state["pending"], state["current"], state["backup"]):
            if (
                version
                and version not in candidates
                and version not in state["bad"]
                and os.path.isfile(os.path.join(ollama_dir(version, self.base), "ollama.exe"))
            ):
                candidates.append(version)
        previous = dict(self._status)
        self._set(STARTING)
        for version in candidates:
            if self._closed:
                return False
            process = self._start_server(
                ollama_dir(version, self.base),
                self.port,
                models_dir(self.base),
                os.path.join(self.base, "server.log"),
            )
            if wait_for_server(self.own, process, SERVER_START_TIMEOUT):
                self._process = process
                self._started(version)
                with self._lock:
                    self._status = dict(previous, seq=self._status["seq"] + 1)
                return True
            stop_process(process)
            logger.warning("AIA: Ollama %s did not start", version)
            if version != candidates[-1]:
                with self._lock:
                    state = read_state(self.base)
                    if version not in state["bad"]:
                        state["bad"].append(version)  # never tried again
                    if state["pending"] == version:
                        state["pending"] = None
                    write_state(state, self.base)
                remove_tree(ollama_dir(version, self.base), attempts=4)
        self._set(FAILED, reason=SERVER)
        return False

    def _started(self, version):
        """A version came up: it becomes current, the one before it the backup, older go."""
        with self._lock:
            state = read_state(self.base)
            if version != state["current"]:
                if state["current"] and state["current"] not in state["bad"]:
                    state["backup"] = state["current"]
                state["current"] = version
            if state["pending"] == version:
                state["pending"] = None
            if state["backup"] == version:
                state["backup"] = None
            write_state(state, self.base)
            keep = {"ollama-%s" % v for v in (state["current"], state["backup"], state["pending"]) if v}
        if self._update_thread is not None and self._update_thread.is_alive():
            return  # it may be writing a new version just now
        for name in os.listdir(self.base):
            if name.startswith("ollama-") and name not in keep:
                remove_tree(os.path.join(self.base, name), attempts=4)

    def _stop_own(self):
        stop_process(self._process)
        self._process = None

    # --- preparing ---

    def _prepare(self, model):
        try:
            state = read_state(self.base)
            if not state["current"]:
                version = self._latest()
                self._set(DOWNLOADING, stage="ollama", done=0, total=0)
                os.makedirs(self.base, exist_ok=True)
                self._download(
                    version,
                    ollama_dir(version, self.base),
                    lambda done, total: self._update_fields(done=done, total=total),
                    self._stop,
                )
                with self._lock:
                    state = read_state(self.base)
                    state["current"] = version
                    write_state(state, self.base)
            if not self._start_own():
                return
            layers = self._layers(model)
            total = sum(layers.values())
            need = total - present_bytes(layers, self.base)
            free = free_disk(self.base)
            if free is not None and free < need + DISK_RESERVE:
                self._set(
                    PAUSED,
                    reason=NO_SPACE,
                    need=need,
                    free=free,
                    total=total,
                    done=downloaded_bytes(layers, self.base),
                )
                return
            self._pull(model, layers)
        except Interrupted:
            self._interrupted(model)
            return
        except SignatureError as e:
            logger.error("AIA: Ollama's files are not signed by %s: %s", SIGNER, e)
            self._set(FAILED, reason=SIGNATURE)
            return
        except PullError as e:
            if self._stop.is_set():
                self._interrupted(model)
            elif _no_space(str(e)):
                self._set(PAUSED, reason=NO_SPACE, **self._progress_guess(model))
            else:
                self._set(FAILED, reason=OTHER, error=str(e))
            return
        except (OSError, ValueError):
            if self._stop.is_set():
                self._interrupted(model)
            else:
                logger.info("AIA: preparing the local model failed", exc_info=True)
                self._set(PAUSED, reason=NETWORK, **self._progress_guess(model))
            return
        self._host = self.own
        self._set(READY, own_models=self._own_models(), just_prepared=True)

    def _interrupted(self, model):
        if self._closed:
            return
        if read_state(self.base)["paused"]:
            self._set(PAUSED, reason=USER, **self._progress_guess(model))
        # otherwise a cancel or another choice follows in the queue

    def _pull(self, model, layers: Dict[str, int]):
        layers = dict(layers)
        total = sum(layers.values())
        completed = {d: downloaded_bytes({d: size}, self.base) for d, size in layers.items()}
        samples = []  # (time, done) of the last 20 s, for the remaining time
        self._set(DOWNLOADING, stage="model", done=sum(completed.values()), total=total, eta=None)
        request = urllib.request.Request(
            self.own + "/api/pull",
            data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = _opener(local=True).open(request, timeout=120)
        self._response = response
        try:
            if self._stop.is_set():
                raise Interrupted()
            for line in response:
                if self._stop.is_set():
                    raise Interrupted()
                if not line.strip():
                    continue
                message = json.loads(line.decode("utf-8"))
                if message.get("error"):
                    raise PullError(message["error"])
                digest = message.get("digest")
                if digest and message.get("total"):
                    if digest not in layers:
                        layers[digest] = int(message["total"])
                        total = sum(layers.values())
                    completed[digest] = int(message.get("completed") or 0)
                    done = sum(completed.values())
                    now = time.monotonic()
                    samples.append((now, done))
                    while len(samples) > 2 and now - samples[0][0] > 20:
                        samples.pop(0)
                    eta = None
                    if now - samples[0][0] >= 2:
                        rate = (done - samples[0][1]) / (now - samples[0][0])
                        if rate > 0:
                            eta = (total - done) / rate
                    self._update_fields(done=done, total=total, eta=eta)
                if message.get("status") == "success":
                    return
            if self._stop.is_set():
                raise Interrupted()
            raise OSError("The download ended early")
        finally:
            self._response = None
            _abort(response)

    def _cancel(self, model):
        """Stop the download and delete what it left (Ollama keeps partial files, at their
        full size, even over a restart; its API deletes only finished models)."""
        try:
            digests = list(self._layers(model))
        except Exception:
            digests = None
        blobs = os.path.join(models_dir(self.base), "blobs")
        deadline = time.monotonic() + 10
        while True:
            left = []
            for name in os.listdir(blobs) if os.path.isdir(blobs) else []:
                if "-partial" not in name:
                    continue
                if digests is not None and not any(
                    name.startswith(d.replace(":", "-") + "-partial") for d in digests
                ):
                    continue
                try:
                    os.remove(os.path.join(blobs, name))
                except OSError:
                    left.append(name)
            if not left or time.monotonic() > deadline:
                break
            time.sleep(0.5)  # Ollama closes the file a moment after the connection ends
        with self._lock:
            state = read_state(self.base)
            if state["wanted"] == model:
                state["wanted"] = None
                state["paused"] = False
                write_state(state, self.base)

    def _remove(self, model):
        with self._lock:
            state = read_state(self.base)
            if state["wanted"] == model:
                state["wanted"] = None
                write_state(state, self.base)
        if not has_own_model(model, self.base) or not self._start_own():
            return
        api(self.own, "/api/delete", {"model": model}, method="DELETE", timeout=30)
        if self._host == self.own and self._model == model:
            self._host = None

    # --- updates ---

    def _start_update_check(self):
        if read_state(self.base)["current"] and self._update_thread is None:
            self._update_thread = threading.Thread(
                target=self.check_update, name="AIA Ollama update", daemon=True
            )
            self._update_thread.start()

    def check_update(self):
        """Download a newer Ollama and try it; it is used from the next start. Blocks."""
        try:
            latest = self._latest()
            state = read_state(self.base)
            versions = [v for v in (state["current"], state["pending"]) if v]
            if not versions:
                return
            newest = max(versions, key=version_tuple)
            if version_tuple(latest) <= version_tuple(newest) or latest in state["bad"]:
                return
            target = ollama_dir(latest, self.base)
            self._download(latest, target, lambda done, total: None, threading.Event())
            if self._closed:
                return
            trial_models = os.path.join(self.base, "trial-models")
            port = free_port()
            process = self._start_server(target, port, trial_models)
            try:
                works = wait_for_server(own_host(port), process, TRIAL_TIMEOUT) is not None
            finally:
                stop_process(process)
                remove_tree(trial_models)
            with self._lock:
                state = read_state(self.base)
                if works:
                    state["pending"] = latest
                elif latest not in state["bad"]:
                    state["bad"].append(latest)
                write_state(state, self.base)
            if not works:
                remove_tree(target)
            logger.info(
                "AIA: Ollama %s %s", latest, "is ready for the next start" if works else "did not start"
            )
        except Exception:
            logger.info("AIA: checking for a newer Ollama failed", exc_info=True)


class PullError(Exception):
    """Ollama refused to download the model."""


def _no_space(text: str) -> bool:
    text = text.lower()
    return "no space" in text or "disk" in text or "not enough space" in text


def _abort(response):
    """Close an HTTP response from another thread; shutting the socket down wakes a read
    blocked in it (closing alone does not on Windows)."""
    try:
        response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        response.close()
    except Exception:
        pass


_manager: Optional[LocalModels] = None


def get_manager() -> LocalModels:
    global _manager
    if _manager is None:
        _manager = LocalModels()
    return _manager
