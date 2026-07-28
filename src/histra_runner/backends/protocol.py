from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from ..schema import AnalysisOutputs, ValidationSpec


@dataclass(frozen=True)
class AnalysisPlanItem:
    name: str
    timeout_seconds: float


@dataclass(frozen=True)
class AnalysisPlan:
    analyses: tuple[AnalysisPlanItem, ...]
    mesh_analysis: AnalysisPlanItem | None = None
    validation: ValidationSpec = field(default_factory=ValidationSpec)

    def timeout_for(self, name: str) -> float:
        if self.mesh_analysis is not None and self.mesh_analysis.name == name:
            return self.mesh_analysis.timeout_seconds
        for item in self.analyses:
            if item.name == name:
                return item.timeout_seconds
        if not self.analyses:
            raise KeyError(name)
        # Dependency analyses are implicit in the HRX. Preserve the historical
        # behavior by granting them the largest requested analysis timeout.
        return max(item.timeout_seconds for item in self.analyses)


@dataclass(frozen=True)
class MutationSchedule:
    interfaces_by_analysis: Mapping[str, Mapping[str, Any]]
    foundation_interface_materials: tuple[str, ...]
    scoured_foundation_interface_material: str

    def for_analysis(self, name: str) -> dict[str, Any]:
        return dict(self.interfaces_by_analysis.get(name, {}))


@dataclass(frozen=True)
class OutputRequest:
    outputs_by_analysis: Mapping[str, AnalysisOutputs]

    @property
    def analysis_names(self) -> tuple[str, ...]:
        return tuple(self.outputs_by_analysis)

    def for_analysis(self, name: str) -> AnalysisOutputs:
        return self.outputs_by_analysis[name]


@dataclass(frozen=True)
class BackendExecution:
    """Backend-neutral execution evidence and captured diagnostic streams."""

    backend: str
    stage: str
    analysis: str
    model_path: Path
    started_at: str
    finished_at: str
    duration_seconds: float
    status: str
    return_code: int | None = None
    command: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_manifest_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "backend": self.backend,
            "stage": self.stage,
            "analysis": self.analysis,
            "model_path": str(self.model_path),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "status": self.status,
            "return_code": self.return_code,
        }
        if self.command:
            result["command"] = list(self.command)
        if self.details:
            result["details"] = dict(self.details)
        return result


@dataclass(frozen=True)
class AnalysisJobResult:
    analysis_key: int
    interfaces: Mapping[str, Any] | None
    validation: Mapping[str, Any]
    outputs: Mapping[str, Any]


@dataclass(frozen=True)
class SolverJobResult:
    backend: str
    analyses: Mapping[str, AnalysisJobResult]
    executions: tuple[BackendExecution, ...]
    mutations: tuple[Mapping[str, Any], ...] = ()
    validation: tuple[Mapping[str, Any], ...] = ()
    artifacts: Mapping[str, Path] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class SolverBackend(Protocol):
    name: str

    def validate(self) -> None:
        ...

    def run_job(
        self,
        model_path: Path,
        analysis_plan: AnalysisPlan,
        mutations: MutationSchedule,
        output_request: OutputRequest,
        timeout_seconds: float,
    ) -> SolverJobResult:
        ...
