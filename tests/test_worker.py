import time
from pathlib import Path

from histra_runner.backends import ExecutionResult
from histra_runner.contracts import Claim
from histra_runner.executor import RunnerExecutor
from histra_runner.worker import HeartbeatLoop, Worker


class Backend:
    def __init__(self, fail=False, sleep=0):
        self.fail = fail
        self.sleep = sleep

    def execute(self, package, output_dir):
        time.sleep(self.sleep)
        if self.fail:
            raise RuntimeError("solver failed")
        return ExecutionResult({"ok": True}, {"solver": "fake"}, "log")


class FakeClient:
    def __init__(self, package_bytes, claim, *, no_work=False):
        self.package_bytes = package_bytes
        self.next_claim = None if no_work else Claim.model_validate(claim)
        self.results = None
        self.failure = None
        self.heartbeats = 0

    def register(self, **kwargs):
        return kwargs.get("runner_id") or "generated-runner"

    def claim(self, runner_id):
        value, self.next_claim = self.next_claim, None
        return value

    def download_package(self, claim, runner_id):
        return self.package_bytes

    def heartbeat(self, claim, runner_id):
        self.heartbeats += 1
        return "later"

    def submit_results(self, claim, envelope):
        self.results = envelope
        return {"status": "completed"}

    def submit_failure(self, claim, **kwargs):
        self.failure = kwargs
        return {"status": "failed"}


def claim_from_manifest(manifest):
    return {
        "job_id": manifest["job_id"],
        "attempt_id": manifest["attempt_id"],
        "job_sha256": manifest["job_sha256"],
        "hrx_sha256": manifest["hrx"]["sha256"],
        "lease_expires_at": "later",
        "package_url": "http://server/package",
    }


def test_worker_success_cleans_workspace(valid_package, tmp_path):
    package_path, manifest = valid_package
    client = FakeClient(package_path.read_bytes(), claim_from_manifest(manifest))
    worker = Worker(
        client=client,
        executor=RunnerExecutor(Backend()),
        work_root=tmp_path / "work",
        runner_name="test",
        runner_id="runner-1",
        heartbeat_interval_seconds=0,
    )
    assert worker.run_once() is True
    assert client.results["results"] == {"ok": True}
    assert not (tmp_path / "work" / "job-001" / "attempt-001").exists()


def test_worker_reports_failure(valid_package, tmp_path):
    package_path, manifest = valid_package
    client = FakeClient(package_path.read_bytes(), claim_from_manifest(manifest))
    worker = Worker(
        client=client,
        executor=RunnerExecutor(Backend(fail=True)),
        work_root=tmp_path / "work",
        runner_name="test",
        runner_id="runner-1",
        heartbeat_interval_seconds=0,
    )
    assert worker.run_once() is True
    assert "failed" in str(client.failure["error"])
    assert client.results is None


def test_worker_no_claim_returns_false(valid_package, tmp_path):
    package_path, manifest = valid_package
    client = FakeClient(package_path.read_bytes(), claim_from_manifest(manifest), no_work=True)
    worker = Worker(client, RunnerExecutor(Backend()), tmp_path, "test", runner_id="r")
    assert worker.run_once() is False


def test_heartbeat_loop_runs(valid_package):
    _, manifest = valid_package
    claim = Claim.model_validate(claim_from_manifest(manifest))
    client = FakeClient(b"", claim_from_manifest(manifest))
    with HeartbeatLoop(client, claim, "runner-1", 0.01):
        time.sleep(0.035)
    assert client.heartbeats >= 2
