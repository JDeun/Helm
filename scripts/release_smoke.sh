#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_ROOT="${1:-/tmp/helm-release-smoke}"

echo "[1/10] syntax"
bash -n "$ROOT/install.sh"

echo "[2/10] bytecode"
python3 -m py_compile "$ROOT/helm.py" "$ROOT/helm_workspace.py" "$ROOT/helm_context.py" "$ROOT"/scripts/*.py

echo "[3/10] package install"
python3 -m pip install --user --no-build-isolation --ignore-installed "$ROOT" >/dev/null

echo "[4/10] manifest audit"
python3 "$ROOT/scripts/run_with_profile.py" validate-manifests --json >/dev/null
python3 "$ROOT/scripts/run_with_profile.py" audit-manifest-quality --json >/dev/null

echo "[5/10] demo workspace"
python3 "$ROOT/helm.py" survey --path "$ROOT/examples/demo-workspace" >/dev/null
python3 "$ROOT/helm.py" doctor --path "$ROOT/examples/demo-workspace" >/dev/null
python3 "$ROOT/helm.py" validate --path "$ROOT/examples/demo-workspace" >/dev/null
HELM_WORKSPACE="$ROOT/examples/demo-workspace" python3 "$ROOT/scripts/run_with_profile.py" validate-manifests --json >/dev/null
HELM_WORKSPACE="$ROOT/examples/demo-workspace" python3 "$ROOT/scripts/run_with_profile.py" audit-manifest-quality --json >/dev/null
python3 "$ROOT/helm.py" context --path "$ROOT/examples/demo-workspace" --include notes tasks commands --summary --limit 8 >/dev/null
python3 "$ROOT/helm.py" checkpoint-recommend --path "$ROOT/examples/demo-workspace" >/dev/null
python3 "$ROOT/helm.py" report --path "$ROOT/examples/demo-workspace" --format markdown >/dev/null
python3 "$ROOT/helm.py" health --path "$ROOT/examples/demo-workspace" state --json >/dev/null

echo "[6/10] init smoke workspace"
python3 "$ROOT/helm.py" init --path "$SMOKE_ROOT" >/dev/null

echo "[7/10] onboarding survey"
python3 "$ROOT/helm.py" survey --path "$SMOKE_ROOT" >/dev/null

echo "[8/10] onboarding apply"
python3 "$ROOT/helm.py" onboard --path "$SMOKE_ROOT" --adopt-openclaw "$HOME/.openclaw/workspace" >/dev/null

echo "[9/10] health and memory capture"
python3 "$ROOT/helm.py" health --path "$SMOKE_ROOT" state --json >/dev/null
python3 "$ROOT/helm.py" memory --path "$SMOKE_ROOT" capture-chat --task-name "release smoke memory capture" --path README.md >/dev/null

echo "[10/10] sources"
python3 "$ROOT/helm.py" sources --path "$SMOKE_ROOT" >/dev/null

echo "release smoke passed"
