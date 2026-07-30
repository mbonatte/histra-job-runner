import json
import sys

import pytest

from histra_runner.backends import BackendError, CommandBackend, ExecutionResult, PythonBackend
from histra_runner.executor import RunnerExecutor
from histra_runner.package import validate_package


def python_adapter(package, output_dir):
    assert package.hrx_path.exists()
    return {
        "results": {"value": 42},
        "run": {"adapter": "python"},
        "logs": "python log",
    }


def invalid_adapter(package, output_dir):
    return {"wrong": True}


def make_claim(manifest):
    return {
        "job_id": manifest["job_id"],
        "attempt_id": manifest["attempt_id"],
        "job_sha256": manifest["job_sha256"],
        "hrx_sha256": manifest["hrx"]["sha256"],
        "lease_expires_at": "2026-07-30T13:00:00Z",
        "package_url": "http://server/package",
    }


def test_python_backend_and_executor(valid_package, tmp_path):
    package, manifest = valid_package
    outcome = RunnerExecutor(PythonBackend("test_backends_executor:python_adapter")).execute_package(
        package,
        tmp_path / "work",
        runner_id="runner-1",
        claim=make_claim(manifest),
    )
    assert outcome.envelope["results"] == {"value": 42}
    assert outcome.envelope["job_sha256"] == manifest["job_sha256"]
    saved = json.loads((outcome.output_dir / "result-envelope.json").read_text())
    assert saved == outcome.envelope


def test_invalid_python_backend_result_is_rejected(valid_package, tmp_path):
    package, manifest = valid_package
    backend = PythonBackend("test_backends_executor:invalid_adapter")
    with pytest.raises(BackendError, match="invalid mapping"):
        RunnerExecutor(backend).execute_package(
            package, tmp_path / "work", runner_id="r", claim=make_claim(manifest)
        )


def test_missing_python_backend_is_rejected():
    with pytest.raises(BackendError, match="cannot load"):
        PythonBackend("no_such_module:function")


def test_command_backend_reads_required_outputs(valid_package, tmp_path):
    package_path, _ = valid_package
    package = validate_package(package_path, tmp_path / "input")
    script = (
        "import json,pathlib,sys; "
        "out=pathlib.Path(sys.argv[1]); out.mkdir(parents=True,exist_ok=True); "
        "(out/'results.json').write_text(json.dumps(dict(force=12.5))); "
        "(out/'run.json').write_text(json.dumps(dict(solver='fake'))); "
        "print('done')"
    )
    backend = CommandBackend([sys.executable, "-c", script, "{output}"])
    result = backend.execute(package, tmp_path / "output")
    assert result.results["force"] == 12.5
    assert result.run["exit_code"] == 0
    assert "done" in result.logs


def test_command_backend_nonzero_is_rejected(valid_package, tmp_path):
    package_path, _ = valid_package
    package = validate_package(package_path, tmp_path / "input")
    backend = CommandBackend([sys.executable, "-c", "import sys;sys.exit(7)"])
    with pytest.raises(BackendError, match="code 7"):
        backend.execute(package, tmp_path / "output")


def test_command_backend_requires_output_files(valid_package, tmp_path):
    package_path, _ = valid_package
    package = validate_package(package_path, tmp_path / "input")
    backend = CommandBackend([sys.executable, "-c", "print('nothing')"])
    with pytest.raises(BackendError, match="did not create"):
        backend.execute(package, tmp_path / "output")
