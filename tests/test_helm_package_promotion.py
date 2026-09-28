"""helm is a package, not a module, and its public surface survived the split."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import helm  # noqa: E402


def test_helm_is_a_package() -> None:
    assert hasattr(helm, "__path__"), "helm must be a package so helm.scripts can nest under it"
    assert Path(helm.__file__).name == "__init__.py"


def test_cli_surface_is_still_reachable_from_the_top_level() -> None:
    # The console script is declared as `helm = "helm:main"`; four test modules
    # also reach for build_status_payload / build_state_snapshot_payload.
    for name in ("main", "build_parser", "build_status_payload", "build_state_snapshot_payload"):
        assert callable(getattr(helm, name)), name


def test_importing_helm_does_not_load_the_cli() -> None:
    # Lazy on purpose: every `from helm.scripts.X import ...` would otherwise
    # import the whole subcommand tree. Run in a subprocess because this test
    # module already imported helm at collection time.
    probe = subprocess.run(
        [sys.executable, "-c", "import sys, helm; print('helm.cli' in sys.modules)"],
        capture_output=True, text=True, cwd=str(ROOT), check=True,
    )
    assert probe.stdout.strip() == "False"


def test_python_dash_m_helm_runs_the_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "helm", "--help"],
        capture_output=True, text=True, cwd=str(ROOT), check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage: helm" in result.stdout


def test_source_bundle_detects_a_helm_checkout_by_the_package(tmp_path, monkeypatch) -> None:
    from scripts.source_bundle import _default_registry

    (tmp_path / "helm").mkdir()
    (tmp_path / "helm" / "cli.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _default_registry() == tmp_path / ".helm" / "source-bundles.json"
