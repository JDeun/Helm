#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_ROOT="${1:-/tmp/helm-release-smoke}"
export PYTHONPYCACHEPREFIX="$SMOKE_ROOT/pycache"
export PIP_CACHE_DIR="$SMOKE_ROOT/pip-cache"

PYTHON="${HELM_RELEASE_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
      fi
    fi
  done
fi
if [[ -z "$PYTHON" ]]; then
  echo "Python >= 3.10 is required for release smoke" >&2
  exit 1
fi

echo "[1/13] syntax"
bash -n "$ROOT/install.sh"

echo "[2/13] bytecode"
"$PYTHON" -m py_compile "$ROOT/helm.py" "$ROOT/helm_workspace.py" "$ROOT/helm_context.py" "$ROOT"/scripts/*.py

echo "[3/13] release version consistency"
"$PYTHON" "$ROOT/scripts/release_version_check.py" --root "$ROOT" >/dev/null

echo "[4/13] package build"
rm -rf "$SMOKE_ROOT/dist"
"$PYTHON" -m build --no-isolation --sdist --wheel --outdir "$SMOKE_ROOT/dist" "$ROOT" >/dev/null

echo "[5/13] package metadata check"
"$PYTHON" -m twine check "$SMOKE_ROOT"/dist/* >/dev/null

echo "[6/13] package install"
"$PYTHON" -m venv --system-site-packages "$SMOKE_ROOT/install-venv"
"$SMOKE_ROOT/install-venv/bin/python" -m pip install --no-build-isolation --no-deps "$ROOT" >/dev/null
"$SMOKE_ROOT/install-venv/bin/helm" --help >/dev/null

echo "[7/13] manifest audit"
"$PYTHON" "$ROOT/scripts/run_with_profile.py" validate-manifests --json >/dev/null
"$PYTHON" "$ROOT/scripts/run_with_profile.py" audit-manifest-quality --json >/dev/null

echo "[8/13] demo workspace"
"$PYTHON" "$ROOT/helm.py" survey --path "$ROOT/examples/demo-workspace" >/dev/null
"$PYTHON" "$ROOT/helm.py" doctor --path "$ROOT/examples/demo-workspace" >/dev/null
"$PYTHON" "$ROOT/helm.py" validate --path "$ROOT/examples/demo-workspace" >/dev/null
HELM_WORKSPACE="$ROOT/examples/demo-workspace" "$PYTHON" "$ROOT/scripts/run_with_profile.py" validate-manifests --json >/dev/null
HELM_WORKSPACE="$ROOT/examples/demo-workspace" "$PYTHON" "$ROOT/scripts/run_with_profile.py" audit-manifest-quality --json >/dev/null
"$PYTHON" "$ROOT/helm.py" context --path "$ROOT/examples/demo-workspace" --include notes tasks commands --summary --limit 8 >/dev/null
"$PYTHON" "$ROOT/helm.py" checkpoint-recommend --path "$ROOT/examples/demo-workspace" >/dev/null
"$PYTHON" "$ROOT/helm.py" report --path "$ROOT/examples/demo-workspace" --format markdown >/dev/null
"$PYTHON" "$ROOT/helm.py" health --path "$ROOT/examples/demo-workspace" state --json >/dev/null

echo "[9/13] init smoke workspace"
"$PYTHON" "$ROOT/helm.py" init --path "$SMOKE_ROOT" >/dev/null

echo "[10/13] onboarding survey"
"$PYTHON" "$ROOT/helm.py" survey --path "$SMOKE_ROOT" >/dev/null

echo "[11/13] onboarding apply"
"$PYTHON" "$ROOT/helm.py" onboard --path "$SMOKE_ROOT" --adopt-openclaw "$HOME/.openclaw/workspace" >/dev/null

echo "[12/13] health and memory capture"
"$PYTHON" "$ROOT/helm.py" health --path "$SMOKE_ROOT" state --json >/dev/null
"$PYTHON" "$ROOT/helm.py" memory --path "$SMOKE_ROOT" capture-chat --task-name "release smoke memory capture" --path README.md >/dev/null

echo "[13/13] sources"
"$PYTHON" "$ROOT/helm.py" sources --path "$SMOKE_ROOT" >/dev/null

echo "release smoke passed"
