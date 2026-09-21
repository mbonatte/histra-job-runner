from argparse import Namespace
from pathlib import Path

import pytest

from histra_runner import __version__
from histra_runner.backends import CommandBackend, PythonBackend
from histra_runner.cli import backend_from_args, build_parser
from histra_runner.histra_python_backend import HiStrAPythonBackend


def test_default_backend_is_histra_python():
    args = build_parser().parse_args(["worker", "--once"])
    backend = backend_from_args(args)
    assert isinstance(backend, HiStrAPythonBackend)
    assert backend.default_timeout_seconds == 3600


def test_backend_overrides_remain_available(tmp_path, monkeypatch):
    module = tmp_path / "custom_backend.py"
    module.write_text("def execute(package, output): return {'results': {}, 'run': {}}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    python_args = build_parser().parse_args(
        ["worker", "--once", "--python-backend", "custom_backend:execute"]
    )
    assert isinstance(backend_from_args(python_args), PythonBackend)
    command_args = build_parser().parse_args(
        ["worker", "--once", "--command", "echo ok", "--timeout", "2"]
    )
    assert isinstance(backend_from_args(command_args), CommandBackend)
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["worker", "--python-backend", "a:b", "--command", "echo"]
        )


def test_environment_defaults(monkeypatch):
    monkeypatch.setenv("HISTRA_SERVER_URL", "https://example.test")
    monkeypatch.setenv("HISTRA_RUNNER_ID", "pc-1")
    monkeypatch.setenv("HISTRA_RUNNER_NAME", "PC")
    monkeypatch.setenv("HISTRA_WORK_ROOT", "C:/work")
    args = build_parser().parse_args(["worker", "--once"])
    assert args.server == "https://example.test"
    assert args.runner_id == "pc-1"
    assert args.name == "PC"
    assert args.work_root == "C:/work"


def test_packaging_declares_required_histra_python_dependency():
    root = Path(__file__).parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    assert 'version = "1.2.0"' in pyproject
    assert "histra-python @ git+https://github.com/mbonatte/histra-python.git@" in pyproject
    assert "git" in (root / "Dockerfile").read_text()
    assert __version__ == "1.2.0"
