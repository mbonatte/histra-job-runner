from pathlib import Path
from types import SimpleNamespace

import pytest

from histra_runner.backends import CommandBackend, ExecutionResult, PythonBackend
from histra_runner.errors import BackendError


def package(tmp_path):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    hrx = root / "model.hrx"
    job = root / "job.json"
    hrx.write_text("<x/>")
    job.write_text("{}")
    return SimpleNamespace(root=root, hrx_path=hrx, job_path=job)


def test_command_backend_reads_outputs(tmp_path):
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json,sys,pathlib\n"
        "out=pathlib.Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)\n"
        "(out/'results.json').write_text(json.dumps({'ok':True}))\n"
        "(out/'run.json').write_text(json.dumps({'solver':'fake'}))\n"
        "print('done')\n"
    )
    backend = CommandBackend(["python", str(script), "{output}"])
    result = backend.execute(package(tmp_path), tmp_path / "output")
    assert result.results == {"ok": True}
    assert result.run["exit_code"] == 0
    assert "done" in result.logs
    assert backend.capabilities["backend"] == "command"


def test_command_backend_errors(tmp_path):
    with pytest.raises(ValueError):
        CommandBackend([])
    backend = CommandBackend(["python", "-c", "raise SystemExit(4)"])
    with pytest.raises(BackendError, match="code 4"):
        backend.execute(package(tmp_path), tmp_path / "output")
    with pytest.raises(BackendError, match="placeholder"):
        CommandBackend(["echo", "{missing}"]).execute(package(tmp_path), tmp_path / "o2")


def test_python_backend_accepts_result_and_mapping(tmp_path, monkeypatch):
    module = tmp_path / "adapter_module.py"
    module.write_text(
        "from histra_runner.backends import ExecutionResult\n"
        "def mapping(package, output): return {'results': {'x': 1}, 'run': {'y': 2}}\n"
        "def result(package, output): return ExecutionResult({'x': 2}, {'y': 3}, 'ok')\n"
        "def invalid(package, output): return 1\n"
        "not_callable=4\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert PythonBackend("adapter_module:mapping").execute(package(tmp_path), tmp_path / "o").results == {"x": 1}
    assert PythonBackend("adapter_module:result").execute(package(tmp_path), tmp_path / "o2") == ExecutionResult({"x": 2}, {"y": 3}, "ok")
    with pytest.raises(BackendError, match="must return"):
        PythonBackend("adapter_module:invalid").execute(package(tmp_path), tmp_path / "o3")
    with pytest.raises(BackendError, match="not callable"):
        PythonBackend("adapter_module:not_callable")
    with pytest.raises(BackendError, match="cannot load"):
        PythonBackend("missing:execute")
