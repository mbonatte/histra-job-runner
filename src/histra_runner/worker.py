from __future__ import annotations
import shutil, threading
from dataclasses import dataclass, field
from pathlib import Path
from .contracts import Claim
from .executor import RunnerExecutor
from .network import ServerClient

class HeartbeatLoop:
    def __init__(self, client, claim, runner_id, interval):
        self.client, self.claim, self.runner_id, self.interval = client, claim, runner_id, interval
        self._stop, self._thread, self.error = threading.Event(), None, None
    def __enter__(self):
        if self.interval <= 0: return self
        def run():
            while not self._stop.wait(self.interval):
                try: self.client.heartbeat(self.claim, self.runner_id)
                except Exception as exc:
                    self.error = exc; self._stop.set()
        self._thread = threading.Thread(target=run, daemon=True); self._thread.start(); return self
    def __exit__(self, *_args):
        self._stop.set()
        if self._thread is not None: self._thread.join(timeout=max(1.0, self.interval * 2))

@dataclass
class Worker:
    client: ServerClient
    executor: RunnerExecutor
    work_root: Path
    runner_name: str
    runner_version: str = "1.1.0"
    runner_id: str | None = None
    capabilities: dict = field(default_factory=dict)
    heartbeat_interval_seconds: float = 30.0
    keep_workspaces: bool = False
    def register(self):
        self.runner_id = self.client.register(runner_id=self.runner_id, name=self.runner_name, version=self.runner_version, capabilities=self.capabilities)
        return self.runner_id
    def run_once(self):
        runner_id = self.runner_id or self.register()
        claim = self.client.claim(runner_id)
        if claim is None: return False
        workspace = self.work_root / claim.job_id / claim.attempt_id
        if workspace.exists(): shutil.rmtree(workspace)
        workspace.mkdir(parents=True)
        package_path = workspace / "package.zip"
        try:
            package_path.write_bytes(self.client.download_package(claim, runner_id))
            with HeartbeatLoop(self.client, claim, runner_id, self.heartbeat_interval_seconds) as heartbeat:
                outcome = self.executor.execute_package(package_path, workspace, runner_id=runner_id, claim=claim)
            if heartbeat.error is not None: raise heartbeat.error
            self.client.submit_results(claim, outcome.envelope)
        except Exception as exc:
            self.client.submit_failure(claim, runner_id=runner_id, error=exc)
            if not self.keep_workspaces: shutil.rmtree(workspace, ignore_errors=True)
            return True
        if not self.keep_workspaces: shutil.rmtree(workspace, ignore_errors=True)
        return True
