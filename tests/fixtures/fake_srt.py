#!/usr/bin/env python3
"""A faithful offline fake of the real SRT 1.0.0 CLI contract.

Real usage: ``srt --settings <file> -- <command...>``, where the settings file has
``filesystem.{allowRead,denyRead,allowWrite,denyWrite}`` and
``network.{allowedDomains,deniedDomains}``.

This fixture exists so ``SrtSandboxRuntime``'s translation and invocation code
is exercised against a real external process boundary with genuinely
observable effects, without depending on the real (beta, not installed here)
``srt`` binary. It is not a general-purpose OS sandbox:

* Filesystem write denial is enforced with real OS permission bits (chmod):
  every directory listed in ``denyWrite`` is made read-only for the duration
  of the child process. A child attempting to create a file there gets a real
  ``OSError``, exactly like it would when actually denied.
* Network denial is enforced for Python child commands only, by injecting a
  ``sitecustomize.py`` (via ``PYTHONPATH``) that monkeypatches
  ``socket.socket.connect`` to refuse hosts outside ``allowedDomains``. STMS's
  own capability probe and its Claude/Codex/Pi harness commands are the only
  processes this fixture is ever asked to wrap in tests, and all of them are
  Python, so this is sufficient to prove the adapter's contract end-to-end.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print("fake-srt 1.0.0")
        return 0
    if argv[:1] != ["--settings"] or len(argv) < 4 or argv[2] != "--":
        print("usage: fake_srt --settings FILE -- command...", file=sys.stderr)
        return 2
    settings = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    command = argv[3:]
    filesystem = settings.get("filesystem", {})
    network = settings.get("network", {})
    current = os.path.realpath(os.getcwd())
    allow_read = tuple(os.path.realpath(path) for path in filesystem.get("allowRead", []))
    deny_read = tuple(os.path.realpath(path) for path in filesystem.get("denyRead", []))
    if _inside(current, deny_read) and not _inside(current, allow_read):
        print("current directory denied by fake srt policy", file=sys.stderr)
        return 1
    restored = _guard_deny_write(filesystem.get("denyWrite", []))
    sitecustomize_dir = _install_guards(
        network.get("allowedDomains", []),
        filesystem.get("allowRead", []),
        filesystem.get("denyRead", []),
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = sitecustomize_dir + os.pathsep + env.get("PYTHONPATH", "")
    try:
        return subprocess.run(command, env=env).returncode
    finally:
        for path, mode in restored:
            os.chmod(path, mode)
        shutil.rmtree(sitecustomize_dir, ignore_errors=True)


def _guard_deny_write(deny_write: list[str]) -> list[tuple[str, int]]:
    restored: list[tuple[str, int]] = []
    for raw in deny_write:
        path = Path(raw)
        if not path.is_dir():
            continue
        restored.append((raw, os.stat(path).st_mode))
        os.chmod(path, stat.S_IRUSR | stat.S_IXUSR)
    return restored


def _inside(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def _install_guards(allowed_domains: list[str], allow_read: list[str], deny_read: list[str]) -> str:
    directory = tempfile.mkdtemp(prefix="fake-srt-sitecustomize-")
    (Path(directory) / "sitecustomize.py").write_text(
        "import socket\n"
        "import builtins, io, os, urllib.request\n"
        "from urllib.parse import urlparse\n"
        f"_ALLOWED = set({allowed_domains!r})\n"
        f"_ALLOW_READ = tuple(os.path.realpath(p) for p in {allow_read!r})\n"
        f"_DENY_READ = tuple(os.path.realpath(p) for p in {deny_read!r})\n"
        "def _inside(path, roots):\n"
        "    return any(path == root or path.startswith(root + os.sep) for root in roots)\n"
        "def _check_read(file, mode):\n"
        "    if isinstance(file, (str, bytes, os.PathLike)) and 'r' in mode:\n"
        "        path = os.path.realpath(file)\n"
        "        if _inside(path, _DENY_READ) and not _inside(path, _ALLOW_READ):\n"
        "            raise PermissionError('read denied by fake srt policy: ' + path)\n"
        "_original_open = builtins.open\n"
        "def _guarded_open(file, mode='r', *a, **kw):\n"
        "    _check_read(file, mode)\n"
        "    return _original_open(file, mode, *a, **kw)\n"
        "builtins.open = _guarded_open\n"
        "io.open = _guarded_open\n"
        "class _FakeResponse:\n"
        "    def read(self, size=-1): return b'ok'[:size]\n"
        "def _guarded_urlopen(url, *a, **kw):\n"
        "    host = urlparse(url.full_url if hasattr(url, 'full_url') else str(url)).hostname\n"
        "    if host not in _ALLOWED:\n"
        "        raise PermissionError('network denied by fake srt policy: ' + str(host))\n"
        "    return _FakeResponse()\n"
        "urllib.request.urlopen = _guarded_urlopen\n"
        "_original_connect = socket.socket.connect\n"
        "def _guarded_connect(self, address, *a, **kw):\n"
        "    host = address[0] if isinstance(address, tuple) else address\n"
        "    if host not in _ALLOWED:\n"
        "        raise PermissionError('network denied by fake srt policy: ' + str(host))\n"
        "    return _original_connect(self, address, *a, **kw)\n"
        "socket.socket.connect = _guarded_connect\n",
        encoding="utf-8",
    )
    return directory


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
