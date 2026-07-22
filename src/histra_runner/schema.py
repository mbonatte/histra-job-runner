from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
import hashlib
import re

from .errors import JobValidationError
from .jsonio import read_json

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ModelSpec:
    path: str
    sha256: str | None = None


@dataclass(frozen=True)
class ScourSpec:
    foundation_interface_materials: tuple[str, ...] = ("Foundation_Soil", "Soil")
    scoured_foundation_interface_material: str = "Soil_removed"


@dataclass(frozen=True)
class MeshSpec:
    enabled: bool = True
    analysis_name: str = "StartMesh"
    timeout_seconds: float = 600.0


@dataclass(frozen=True)
class ReactionOutputSpec:
    enabled: bool = True
    all_steps: bool = True
    step: int | None = None


@dataclass(frozen=True)
class DisplacementOutputSpec:
    enabled: bool = True
    all_steps: bool = True
    step: int | None = None
    model_point_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ModalOutputSpec:
    enabled: bool = False
    top_n: int = 3


@dataclass(frozen=True)
class AnalysisOutputs:
    reactions: ReactionOutputSpec = field(default_factory=ReactionOutputSpec)
    displacements: DisplacementOutputSpec = field(default_factory=DisplacementOutputSpec)
    modal_contributions: ModalOutputSpec = field(default_factory=ModalOutputSpec)


@dataclass(frozen=True)
class AnalysisSpec:
    name: str
    timeout_seconds: float = 3600.0
    interfaces: dict[str, Any] = field(default_factory=dict)
    outputs: AnalysisOutputs = field(default_factory=AnalysisOutputs)


@dataclass(frozen=True)
class ValidationSpec:
    require_completed_state: bool = True
    require_results_database: bool = True
    minimum_results_bytes: int = 1


@dataclass(frozen=True)
class JobSpec:
    schema_version: str
    job_id: str
    model: ModelSpec
    analyses: tuple[AnalysisSpec, ...]
    mesh: MeshSpec = field(default_factory=MeshSpec)
    scour: ScourSpec = field(default_factory=ScourSpec)
    validation: ValidationSpec = field(default_factory=ValidationSpec)
    attempt_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolve_model_path(self, package_root: Path) -> Path:
        relative = PurePosixPath(self.model.path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise JobValidationError("model.path must be a safe relative path inside the job package.")
        candidate = (package_root / Path(*relative.parts)).resolve()
        root = package_root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise JobValidationError("model.path escapes the job package.") from exc
        return candidate

    def validate_package(self, package_root: Path, *, verify_hash: bool = True) -> Path:
        model_path = self.resolve_model_path(package_root)
        if not model_path.is_file():
            raise JobValidationError(f"Model file not found: {model_path}")
        if verify_hash and self.model.sha256:
            actual = sha256_file(model_path)
            if actual.lower() != self.model.sha256.lower():
                raise JobValidationError(
                    f"Model SHA-256 mismatch: expected {self.model.sha256}, got {actual}."
                )
        return model_path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JobValidationError(f"{label} must be an object.")
    return value


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise JobValidationError(f"{label} must be a positive number.")
    return float(value)


def _optional_step(data: dict[str, Any], label: str) -> int | None:
    step = data.get("step")
    if step is None:
        return None
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise JobValidationError(f"{label}.step must be a non-negative integer or null.")
    return step



def _scour_delta(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JobValidationError(f"{label} must be a number between 0 and 1.")
    delta = float(value)
    if not 0 <= delta <= 1:
        raise JobValidationError(f"{label} must be between 0 and 1.")
    return delta


def _parse_interfaces(data: Any, label: str) -> dict[str, Any]:
    raw = _mapping(data or {}, label)
    parsed: dict[str, Any] = {}
    supported_modes = {"uniform", "left", "right", "upstream", "downstream"}
    for pier, scour_config in raw.items():
        if not isinstance(pier, str) or not pier.strip():
            raise JobValidationError(f"{label} pier names must be non-empty strings.")
        if isinstance(scour_config, dict):
            if not scour_config:
                raise JobValidationError(f"{label}.{pier} must define at least one scour mode.")
            modes: dict[str, float] = {}
            for mode, delta in scour_config.items():
                if mode not in supported_modes:
                    expected = ", ".join(sorted(supported_modes))
                    raise JobValidationError(
                        f"{label}.{pier} has unsupported mode '{mode}'. Expected: {expected}."
                    )
                modes[mode] = _scour_delta(delta, f"{label}.{pier}.{mode}")
            parsed[pier] = modes
        else:
            parsed[pier] = _scour_delta(scour_config, f"{label}.{pier}")
    return parsed


def _parse_outputs(data: Any, label: str) -> AnalysisOutputs:
    raw = _mapping(data or {}, label)

    reaction_data = _mapping(raw.get("reactions", {}), f"{label}.reactions")
    reactions = ReactionOutputSpec(
        enabled=bool(reaction_data.get("enabled", True)),
        all_steps=bool(reaction_data.get("all_steps", True)),
        step=_optional_step(reaction_data, f"{label}.reactions"),
    )

    displacement_data = _mapping(raw.get("displacements", {}), f"{label}.displacements")
    raw_ids = displacement_data.get("model_point_ids", [])
    if not isinstance(raw_ids, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in raw_ids
    ):
        raise JobValidationError(f"{label}.displacements.model_point_ids must be a list of integers.")
    displacements = DisplacementOutputSpec(
        enabled=bool(displacement_data.get("enabled", True)),
        all_steps=bool(displacement_data.get("all_steps", True)),
        step=_optional_step(displacement_data, f"{label}.displacements"),
        model_point_ids=tuple(raw_ids),
    )

    modal_data = _mapping(raw.get("modal_contributions", {}), f"{label}.modal_contributions")
    top_n = modal_data.get("top_n", 3)
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise JobValidationError(f"{label}.modal_contributions.top_n must be a positive integer.")
    modal = ModalOutputSpec(
        enabled=bool(modal_data.get("enabled", False)),
        top_n=top_n,
    )
    return AnalysisOutputs(reactions=reactions, displacements=displacements, modal_contributions=modal)


def job_spec_from_dict(data: Any) -> JobSpec:
    raw = _mapping(data, "job")
    schema_version = str(raw.get("schema_version", ""))
    if schema_version != "1.0":
        raise JobValidationError("Only job schema_version '1.0' is supported.")

    job_id = raw.get("job_id")
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise JobValidationError(
            "job_id must contain only letters, numbers, dot, underscore or hyphen (max 128)."
        )

    attempt_id = raw.get("attempt_id")
    if attempt_id is not None and (
        not isinstance(attempt_id, str) or not _JOB_ID_RE.fullmatch(attempt_id)
    ):
        raise JobValidationError("attempt_id uses an invalid format.")

    model_data = _mapping(raw.get("model"), "model")
    model_path = model_data.get("path")
    if not isinstance(model_path, str) or not model_path.strip():
        raise JobValidationError("model.path is required.")
    model_hash = model_data.get("sha256")
    if model_hash is not None and (
        not isinstance(model_hash, str) or not _SHA256_RE.fullmatch(model_hash)
    ):
        raise JobValidationError("model.sha256 must be a 64-character hexadecimal digest.")

    raw_analyses = raw.get("analyses")
    if not isinstance(raw_analyses, list) or not raw_analyses:
        raise JobValidationError("analyses must be a non-empty list.")
    analyses: list[AnalysisSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_analyses):
        analysis_data = _mapping(item, f"analyses[{index}]")
        name = analysis_data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise JobValidationError(f"analyses[{index}].name is required.")
        if name in seen:
            raise JobValidationError(f"Duplicate analysis name: {name}")
        seen.add(name)
        analyses.append(
            AnalysisSpec(
                name=name,
                timeout_seconds=_positive_number(
                    analysis_data.get("timeout_seconds", 3600),
                    f"analyses[{index}].timeout_seconds",
                ),
                interfaces=_parse_interfaces(
                    analysis_data.get("interfaces", {}), f"analyses[{index}].interfaces"
                ),
                outputs=_parse_outputs(
                    analysis_data.get("outputs", {}), f"analyses[{index}].outputs"
                ),
            )
        )

    mesh_data = _mapping(raw.get("mesh", {}), "mesh")
    mesh_name = mesh_data.get("analysis_name", "StartMesh")
    if not isinstance(mesh_name, str) or not mesh_name.strip():
        raise JobValidationError("mesh.analysis_name must be a non-empty string.")
    mesh = MeshSpec(
        enabled=bool(mesh_data.get("enabled", True)),
        analysis_name=mesh_name,
        timeout_seconds=_positive_number(
            mesh_data.get("timeout_seconds", 600), "mesh.timeout_seconds"
        ),
    )

    scour_data = _mapping(raw.get("scour", {}), "scour")
    raw_default_materials = scour_data.get(
        "foundation_interface_materials", ["Foundation_Soil", "Soil"]
    )
    if (
        not isinstance(raw_default_materials, list)
        or not raw_default_materials
        or any(not isinstance(item, str) or not item.strip() for item in raw_default_materials)
    ):
        raise JobValidationError(
            "scour.foundation_interface_materials must be a non-empty list of material names."
        )
    scoured_material = scour_data.get(
        "scoured_foundation_interface_material", "Soil_removed"
    )
    if not isinstance(scoured_material, str) or not scoured_material.strip():
        raise JobValidationError(
            "scour.scoured_foundation_interface_material must be a non-empty string."
        )
    scour = ScourSpec(
        foundation_interface_materials=tuple(raw_default_materials),
        scoured_foundation_interface_material=scoured_material,
    )

    validation_data = _mapping(raw.get("validation", {}), "validation")
    minimum_results_bytes = validation_data.get("minimum_results_bytes", 1)
    if (
        isinstance(minimum_results_bytes, bool)
        or not isinstance(minimum_results_bytes, int)
        or minimum_results_bytes < 0
    ):
        raise JobValidationError("validation.minimum_results_bytes must be a non-negative integer.")
    validation = ValidationSpec(
        require_completed_state=bool(validation_data.get("require_completed_state", True)),
        require_results_database=bool(validation_data.get("require_results_database", True)),
        minimum_results_bytes=minimum_results_bytes,
    )

    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise JobValidationError("metadata must be an object.")

    return JobSpec(
        schema_version=schema_version,
        job_id=job_id,
        attempt_id=attempt_id,
        model=ModelSpec(path=model_path, sha256=model_hash),
        mesh=mesh,
        scour=scour,
        validation=validation,
        analyses=tuple(analyses),
        metadata=metadata,
    )


def load_job_spec(path: str | Path) -> JobSpec:
    job_path = Path(path).resolve()
    if not job_path.is_file():
        raise JobValidationError(f"Job file not found: {job_path}")
    try:
        return job_spec_from_dict(read_json(job_path))
    except JobValidationError:
        raise
    except Exception as exc:
        raise JobValidationError(f"Could not read job file {job_path}: {exc}") from exc
