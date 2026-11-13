# Copyright (c) 2026 Jakub Šenkýř
# SPDX-License-Identifier: MIT
"""Thonny AIA runs next to the regular Thonny (cortex-zamestnani#120): its single-instance
file must not be the one of the regular Thonny ("thonny-<user>")."""

import os

import thonny


def test_ipc_dir_name_is_not_upstreams():
    assert thonny.ipc_dir_name("senkyr") == "thonny-aia-senkyr"
    assert thonny.ipc_dir_name("senkyr") != "thonny-senkyr"


def test_ipc_file_path_in_own_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(thonny, "_IPC_FILE", None)
    monkeypatch.setattr(thonny, "_get_local_appdata_dir", lambda: str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    for name in ("LOGNAME", "USER", "LNAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USERNAME", "zak")
    path = thonny.get_ipc_file_path()
    assert path == os.path.join(str(tmp_path), "thonny-aia-zak", "ipc.sock")
    assert os.path.isdir(os.path.dirname(path))
