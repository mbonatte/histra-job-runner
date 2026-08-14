from dataclasses import dataclass

from histra_runner.backends import ExecutionResult
from histra_runner.executor import RunnerExecutor
from histra_runner.worker import Worker


class Backend:
    def execute(self, package, output_dir):
        assert package.job["job_id"] == "job-001"
        return ExecutionResult({"status": "completed"}, {"solver": "fake"}, "log")


class FakeClient:
    def __init__(self, package_bytes, claim):
        self.package_bytes = package_bytes
        self.claim_value = claim
        self.results = None
        self.failure = None
        self.register_payload = None
    def register(self, **kwargs):
        self.register_payload = kwargs
        return kwargs["runner_id"] or "generated"
    def claim(self, _runner_id):
        value, self.claim_value = self.claim_value, None
        return value
    def download_package(self, _claim, _runner_id):
        return self.package_bytes
    def heartbeat(self, *_args):
        return "later"
    def submit_results(self, _claim, envelope):
        self.results = envelope
    def submit_failure(self, _claim, **kwargs):
        self.failure = kwargs


def test_executor_and_worker_success(package_factory, job_document, hrx_bytes, tmp_path):
    package, claim = package_factory(job_document, hrx_bytes)
    client = FakeClient(package.read_bytes(), claim)
    worker = Worker(
        client=client,
        executor=RunnerExecutor(Backend()),
        work_root=tmp_path / "work",
        runner_name="worker",
        runner_id="r1",
        capabilities={"backend": "test"},
        heartbeat_interval_seconds=0,
    )
    assert worker.register() == "r1"
    assert worker.run_once() is True
    assert client.results["results"]["status"] == "completed"
    assert client.register_payload["capabilities"] == {"backend": "test"}
    assert worker.run_once() is False


def test_worker_reports_failure(package_factory, job_document, hrx_bytes, tmp_path):
    class Failing:
        def execute(self, package, output_dir):
            raise RuntimeError("solver failed")
    package, claim = package_factory(job_document, hrx_bytes)
    client = FakeClient(package.read_bytes(), claim)
    worker = Worker(
        client=client,
        executor=RunnerExecutor(Failing()),
        work_root=tmp_path / "work",
        runner_name="worker",
        heartbeat_interval_seconds=0,
        keep_workspaces=True,
    )
    assert worker.run_once() is True
    assert "solver failed" in str(client.failure["error"])
    assert list((tmp_path / "work").rglob("package.zip"))
