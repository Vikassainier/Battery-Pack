"""The one-command launcher (scripts/run_local.py): its helpers, without creating an environment or starting a server."""
import importlib.util
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_local", ROOT / "scripts" / "run_local.py")
run_local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_local)


def test_port_probe_and_free_port_skip_a_busy_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        assert run_local.port_in_use("127.0.0.1", busy) and run_local.port_in_use("0.0.0.0", busy)
        found = run_local.free_port("127.0.0.1", busy)
        assert found != busy and not run_local.port_in_use("127.0.0.1", found)
    assert not run_local.port_in_use("127.0.0.1", busy)                       # released again


def test_no_free_port_gives_a_clear_message(monkeypatch):
    monkeypatch.setattr(run_local, "port_in_use", lambda host, port: True)
    with pytest.raises(SystemExit) as e:
        run_local.free_port("127.0.0.1", 8000, tries=3)
    assert "No free port" in str(e.value)


def test_virtualenv_interpreter_path_matches_the_platform(monkeypatch):
    monkeypatch.setattr(run_local.os, "name", "nt")
    assert run_local.venv_python().parts[-2:] == ("Scripts", "python.exe")
    monkeypatch.setattr(run_local.os, "name", "posix")
    assert run_local.venv_python().parts[-2:] == ("bin", "python")
    assert run_local.venv_python().parent.parent == run_local.VENV == ROOT / ".venv"


def test_help_exits_cleanly_and_lists_the_options(capsys):
    with pytest.raises(SystemExit) as e:
        run_local.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for opt in ("--port", "--host", "--no-browser", "--reload", "--reinstall"):
        assert opt in out


def test_default_host_is_loopback_only():
    src = (ROOT / "scripts" / "run_local.py").read_text()
    assert 'default="127.0.0.1"' in src                                       # never exposes the server to the network unless asked
