from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _python_sources(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


@pytest.mark.parametrize(
    "relative_path",
    [
        "examples/agent.py",
        "examples/deep_research.py",
        "examples/streamlit_app.py",
        "scripts/admin.py",
        "scripts/export_openapi.py",
    ],
)
def test_example_and_script_files_compile(relative_path: str, tmp_path: Path) -> None:
    source = ROOT / relative_path
    py_compile.compile(str(source), cfile=str(tmp_path / "out.pyc"), doraise=True)


def test_every_python_source_file_compiles(tmp_path: Path) -> None:
    sources = _python_sources(ROOT / "app") + _python_sources(ROOT / "scripts")
    for source in sources:
        py_compile.compile(
            str(source),
            cfile=str(tmp_path / f"{source.stem}.pyc"),
            doraise=True,
        )
    assert sources
