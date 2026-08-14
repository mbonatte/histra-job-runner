from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .backends import ExecutionBackend
from .contracts import Claim, ResultEnvelope
from .package import PackageContents, validate_package

@dataclass(frozen=True)
class RunOutcome:
    envelope: dict[str, Any]
    package: PackageContents
    output_dir: Path

class RunnerExecutor:
    def __init__(self, backend: ExecutionBackend): self.backend = backend
    def execute_package(self, package_path, workspace, *, runner_id, claim):
        claim = claim if isinstance(claim, Claim) else Claim.model_validate(claim)
        workspace = Path(workspace)
        input_dir, output_dir = workspace / "input", workspace / "output"
        package = validate_package(package_path, input_dir, expected_job_id=claim.job_id, expected_attempt_id=claim.attempt_id, expected_job_sha256=claim.job_sha256, expected_hrx_sha256=claim.hrx_sha256)
        execution = self.backend.execute(package, output_dir)
        envelope = ResultEnvelope(runner_id=runner_id, job_id=package.manifest.job_id, attempt_id=package.manifest.attempt_id, job_sha256=package.manifest.job_sha256, hrx_sha256=package.manifest.hrx.sha256, results=execution.results, run=execution.run, logs=execution.logs).model_dump(mode="json")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result-envelope.json").write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return RunOutcome(envelope, package, output_dir)
