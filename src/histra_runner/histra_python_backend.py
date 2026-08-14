"""Built-in adapter from canonical JOB workflows to :mod:`histra-python`.

The Runner deliberately owns orchestration concerns (JOB validation, workflow
translation, result envelopes), while ``histra-python`` owns HRX loading and
numerical execution.
"""
from __future__ import annotations

import importlib
import json
import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .backends import ExecutionResult
from .errors import BackendError
from .package import PackageContents


@dataclass(frozen=True)
class StepOutputRequest:
    enabled: bool = True
    all_steps: bool = True
    step: int | None = None


@dataclass(frozen=True)
class DisplacementOutputRequest(StepOutputRequest):
    model_point_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class AnalysisOutputRequest:
    reactions: StepOutputRequest
    displacements: DisplacementOutputRequest
    modal_contributions: StepOutputRequest


@dataclass(frozen=True)
class SolverApi:
    version: str
    PythonAnalysisRequest: type
    ConcreteInterfaceMutation: type
    run_python_solver_job: Any


def _load_solver_api() -> SolverApi:
    try:
        module = importlib.import_module("histra")
        return SolverApi(
            version=str(getattr(module, "__version__", "unknown")),
            PythonAnalysisRequest=getattr(module, "PythonAnalysisRequest"),
            ConcreteInterfaceMutation=getattr(module, "ConcreteInterfaceMutation"),
            run_python_solver_job=getattr(module, "run_python_solver_job"),
        )
    except (ImportError, AttributeError) as exc:
        raise BackendError(
            "histra-python is not installed correctly; reinstall histra-job-runner "
            "so its required histra-python dependency is installed"
        ) from exc


def _positive_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BackendError(f"{label} must be a positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise BackendError(f"{label} must be a positive finite number")
    return result


def _integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise BackendError(f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BackendError(f"{label} must be an integer") from exc
    if minimum is not None and result < minimum:
        raise BackendError(f"{label} must be at least {minimum}")
    return result


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BackendError(f"{label} must be a JSON object")
    return value


def _step_request(value: Any, *, default_enabled: bool, label: str) -> StepOutputRequest:
    if value is None:
        return StepOutputRequest(enabled=default_enabled)
    if isinstance(value, bool):
        return StepOutputRequest(enabled=value)
    item = _mapping(value, label)
    enabled = bool(item.get("enabled", default_enabled))
    all_steps = bool(item.get("all_steps", True))
    step_raw = item.get("step")
    step = None if step_raw is None else _integer(step_raw, f"{label}.step", minimum=0)
    if all_steps and step is not None:
        raise BackendError(f"{label} cannot set both all_steps=true and step")
    return StepOutputRequest(enabled=enabled, all_steps=all_steps, step=step)


def _displacement_request(value: Any) -> DisplacementOutputRequest:
    if value is None:
        return DisplacementOutputRequest()
    if isinstance(value, bool):
        return DisplacementOutputRequest(enabled=value)
    item = _mapping(value, "outputs.displacements")
    base = _step_request(
        item,
        default_enabled=True,
        label="outputs.displacements",
    )
    ids_raw = item.get("model_point_ids", ())
    if ids_raw is None:
        ids_raw = ()
    if isinstance(ids_raw, (str, bytes)) or not isinstance(ids_raw, Iterable):
        raise BackendError("outputs.displacements.model_point_ids must be an array")
    ids = tuple(
        _integer(value, "outputs.displacements.model_point_ids[]", minimum=0)
        for value in ids_raw
    )
    if len(ids) != len(set(ids)):
        raise BackendError("outputs.displacements.model_point_ids contains duplicates")
    return DisplacementOutputRequest(
        enabled=base.enabled,
        all_steps=base.all_steps,
        step=base.step,
        model_point_ids=ids,
    )


def _output_request(value: Any) -> AnalysisOutputRequest:
    item: Mapping[str, Any]
    if value is None:
        item = {}
    else:
        item = _mapping(value, "outputs")
    unknown = set(item) - {"reactions", "displacements", "modal_contributions"}
    if unknown:
        raise BackendError(f"unsupported output request fields: {sorted(unknown)!r}")
    return AnalysisOutputRequest(
        reactions=_step_request(
            item.get("reactions"),
            default_enabled=True,
            label="outputs.reactions",
        ),
        displacements=_displacement_request(item.get("displacements")),
        modal_contributions=_step_request(
            item.get("modal_contributions"),
            default_enabled=False,
            label="outputs.modal_contributions",
        ),
    )


def _analysis_name(value: Any, index: int) -> tuple[str, Mapping[str, Any]]:
    if isinstance(value, str):
        name = value.strip()
        item: Mapping[str, Any] = {}
    else:
        item = _mapping(value, f"workflow.analyses[{index}]")
        raw_name = item.get("name", item.get("id"))
        name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name:
        raise BackendError(
            f"workflow.analyses[{index}] must define a non-empty name or id"
        )
    return name, item


def _normalize_mutation_specs(value: Any, label: str) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise BackendError(f"{label} must be an object or array of objects")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        result.append(_mapping(item, f"{label}[{index}]"))
    return tuple(result)


def _mutation(spec: Mapping[str, Any], api: SolverApi, label: str) -> Any:
    unknown = set(spec) - {"interface_keys", "material_key", "preserve_committed_state"}
    if unknown:
        raise BackendError(f"unsupported {label} fields: {sorted(unknown)!r}")
    keys_raw = spec.get("interface_keys")
    if isinstance(keys_raw, (str, bytes)) or not isinstance(keys_raw, Iterable):
        raise BackendError(f"{label}.interface_keys must be a non-empty array")
    keys = tuple(_integer(value, f"{label}.interface_keys[]", minimum=0) for value in keys_raw)
    if not keys:
        raise BackendError(f"{label}.interface_keys must be a non-empty array")
    if len(keys) != len(set(keys)):
        raise BackendError(f"{label}.interface_keys contains duplicates")
    if "material_key" not in spec:
        raise BackendError(f"{label}.material_key is required")
    return api.ConcreteInterfaceMutation(
        interface_keys=keys,
        material_key=_integer(spec["material_key"], f"{label}.material_key", minimum=0),
        preserve_committed_state=bool(spec.get("preserve_committed_state", True)),
    )


def _workflow_plan(
    job: Mapping[str, Any],
    api: SolverApi,
    *,
    default_timeout_seconds: float,
) -> tuple[tuple[Any, ...], dict[str, tuple[Any, ...]], float, int]:
    workflow = _mapping(job.get("workflow"), "workflow")
    analyses_raw = workflow.get("analyses")
    if isinstance(analyses_raw, (str, bytes)) or not isinstance(analyses_raw, Iterable):
        raise BackendError("workflow.analyses must be a non-empty array")
    analyses_values = tuple(analyses_raw)
    if not analyses_values:
        raise BackendError("workflow.analyses must contain at least one analysis")

    default_analysis_timeout = _positive_float(
        workflow.get("analysis_timeout_seconds", default_timeout_seconds),
        "workflow.analysis_timeout_seconds",
    )
    shared_outputs = workflow.get("outputs", workflow.get("output_request"))
    requests: list[Any] = []
    per_analysis_items: dict[str, Mapping[str, Any]] = {}
    names_casefold: set[str] = set()
    for index, raw in enumerate(analyses_values):
        name, item = _analysis_name(raw, index)
        folded = name.casefold()
        if folded in names_casefold:
            raise BackendError(f"workflow.analyses contains duplicate analysis {name!r}")
        names_casefold.add(folded)
        per_analysis_items[name] = item
        timeout = _positive_float(
            item.get("timeout_seconds", default_analysis_timeout),
            f"workflow.analyses[{index}].timeout_seconds",
        )
        output_value = item.get("outputs", item.get("output_request", shared_outputs))
        requests.append(
            api.PythonAnalysisRequest(
                name=name,
                output_request=_output_request(output_value),
                timeout_seconds=timeout,
            )
        )

    mutation_map: dict[str, list[Any]] = {name: [] for name in per_analysis_items}
    top_level = workflow.get("interface_mutations", {})
    if top_level is not None:
        top_mapping = _mapping(top_level, "workflow.interface_mutations")
        canonical_names = {name.casefold(): name for name in per_analysis_items}
        for supplied_name, specs in top_mapping.items():
            if not isinstance(supplied_name, str):
                raise BackendError("workflow.interface_mutations keys must be strings")
            canonical_name = canonical_names.get(supplied_name.casefold())
            if canonical_name is None:
                raise BackendError(
                    f"interface mutations reference unrequested analysis {supplied_name!r}"
                )
            for index, spec in enumerate(
                _normalize_mutation_specs(
                    specs,
                    f"workflow.interface_mutations[{supplied_name!r}]",
                )
            ):
                mutation_map[canonical_name].append(
                    _mutation(
                        spec,
                        api,
                        f"workflow.interface_mutations[{supplied_name!r}][{index}]",
                    )
                )

    for name, item in per_analysis_items.items():
        for index, spec in enumerate(
            _normalize_mutation_specs(
                item.get("interface_mutations"),
                f"analysis {name!r}.interface_mutations",
            )
        ):
            mutation_map[name].append(
                _mutation(spec, api, f"analysis {name!r}.interface_mutations[{index}]")
            )

    mutations = {name: tuple(values) for name, values in mutation_map.items() if values}
    inferred_job_timeout = sum(float(request.timeout_seconds) for request in requests)
    job_timeout = _positive_float(
        workflow.get("timeout_seconds", max(default_timeout_seconds, inferred_job_timeout)),
        "workflow.timeout_seconds",
    )
    combination_row = _integer(
        workflow.get("combination_row", 1),
        "workflow.combination_row",
        minimum=1,
    )
    return tuple(requests), mutations, job_timeout, combination_row


def _jsonable(value: Any, *, path: str = "result") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BackendError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Enum):
        return _jsonable(value.value, path=path)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value), path=path)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, (str, int, float, bool)):
                raise BackendError(f"{path} contains a non-JSON object key")
            result[str(key)] = _jsonable(item, path=f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item, path=f"{path}[]") for item in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist(), path=path)
    if hasattr(value, "item"):
        return _jsonable(value.item(), path=path)
    raise BackendError(f"{path} contains unsupported value {type(value).__name__}")


def _execution_summary(execution: Any) -> dict[str, Any]:
    steps = tuple(getattr(execution, "steps", ()) or ())
    committed = tuple(getattr(execution, "committed_steps", ()) or ())
    outcome = getattr(execution, "outcome", None)
    return {
        "analysis_key": int(getattr(execution, "analysis_key")),
        "analysis_name": str(getattr(execution, "analysis_name")),
        "exit_code": int(getattr(execution, "code")),
        "outcome": _jsonable(outcome),
        "completed": bool(getattr(execution, "completed")),
        "message": getattr(execution, "message", None),
        "runtime_seconds": float(getattr(execution, "runtime_seconds")),
        "step_count": len(steps),
        "committed_step_count": len(committed),
    }


class HiStrAPythonBackend:
    """Execute a canonical JOB with the bundled ``histra-python`` dependency."""

    def __init__(
        self,
        *,
        default_timeout_seconds: float = 3600.0,
        solver_api: SolverApi | None = None,
    ):
        self.default_timeout_seconds = _positive_float(
            default_timeout_seconds,
            "default_timeout_seconds",
        )
        self._solver_api = solver_api

    @property
    def solver_api(self) -> SolverApi:
        if self._solver_api is None:
            self._solver_api = _load_solver_api()
        return self._solver_api

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "backend": "histra-python",
            "histra_python_version": self.solver_api.version,
            "supports": [
                "static-nonlinear",
                "reactions",
                "model-point-displacements",
                "interface-material-mutations",
            ],
            "serialized_in_process": True,
        }

    def execute(self, package: PackageContents, output_dir: Path) -> ExecutionResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        api = self.solver_api
        requests, mutations, job_timeout, combination_row = _workflow_plan(
            package.job,
            api,
            default_timeout_seconds=self.default_timeout_seconds,
        )
        started = time.perf_counter()
        live_logs: list[str] = []
        try:
            solver_result = api.run_python_solver_job(
                package.hrx_path,
                requests,
                timeout_seconds=job_timeout,
                interface_mutations=mutations or None,
                combination_row=combination_row,
                on_log=live_logs.append,
            )
        except Exception as exc:
            details = "\n".join(live_logs[-50:])
            suffix = f"\nRecent solver log:\n{details}" if details else ""
            raise BackendError(f"histra-python execution failed: {exc}{suffix}") from exc
        elapsed = time.perf_counter() - started

        analyses: dict[str, Any] = {}
        for name, analysis_result in solver_result.analyses.items():
            analyses[str(name)] = {
                "execution": _execution_summary(analysis_result.execution),
                "outputs": _jsonable(analysis_result.outputs, path=f"analyses.{name}.outputs"),
                "mutations": _jsonable(
                    tuple(getattr(analysis_result, "mutations", ()) or ()),
                    path=f"analyses.{name}.mutations",
                ),
            }
        executions = [_execution_summary(item) for item in solver_result.executions]
        logs = "\n".join(str(line) for line in solver_result.logs)
        if not logs and live_logs:
            logs = "\n".join(live_logs)

        results = {
            "backend": "histra-python",
            "analyses": analyses,
        }
        run = {
            "backend": "histra-python",
            "histra_python_version": api.version,
            "job_id": package.job.get("job_id"),
            "model_path": package.manifest.hrx.path,
            "analysis_order": [item["analysis_name"] for item in executions],
            "executions": executions,
            "elapsed_seconds": elapsed,
            "solver_metadata": _jsonable(solver_result.metadata, path="solver_metadata"),
        }
        # These files are not required by RunnerExecutor, but make retained
        # workspaces directly inspectable and preserve parity with command adapters.
        (output_dir / "results.json").write_text(
            json.dumps(results, indent=2, allow_nan=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (output_dir / "run.json").write_text(
            json.dumps(run, indent=2, allow_nan=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if logs:
            (output_dir / "solver.log").write_text(logs + "\n", encoding="utf-8")
        return ExecutionResult(results=results, run=run, logs=logs)
