from __future__ import annotations

from pathlib import Path

from histra_runner.config import load_runner_config


def test_existing_solver_toml_shape_is_preserved(tmp_path):
    config_path = tmp_path / "runner.toml"
    config_path.write_text(
        """
[solver]
executable = "./SolverHistra.exe"
mode = "local"
close_without_ask = true

[runner]
workspace_root = "./work"
""",
        encoding="utf-8",
    )
    config = load_runner_config(config_path)
    assert config.solver.executable == (tmp_path / "SolverHistra.exe").resolve()
    assert config.workspace_root == (tmp_path / "work").resolve()


def test_python_backend_config_does_not_require_solver_section(tmp_path):
    config_path = tmp_path / "runner-python.toml"
    config_path.write_text(
        """
[backend]
type = "python"

[python]
combination_row = 2

[runner]
workspace_root = "./work"
""",
        encoding="utf-8",
    )
    config = load_runner_config(config_path)
    assert config.backend.type == "python"
    assert config.python.combination_row == 2
    assert config.solver is None
    config.validate(require_solver_files=True)
