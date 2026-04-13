#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HELM_REPO_URL:-https://github.com/JDeun/Helm.git}"
WORKSPACE_PATH="${HELM_WORKSPACE:-$HOME/.helm/workspace}"
SKIP_INIT="${HELM_SKIP_INIT:-0}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace)
      if [ "$#" -lt 2 ]; then
        echo "--workspace requires a path argument." >&2
        exit 1
      fi
      WORKSPACE_PATH="$2"
      shift 2
      ;;
    --repo)
      if [ "$#" -lt 2 ]; then
        echo "--repo requires a git URL." >&2
        exit 1
      fi
      REPO_URL="$2"
      shift 2
      ;;
    --skip-init)
      SKIP_INIT=1
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: install.sh [--workspace PATH] [--repo GIT_URL] [--skip-init]

Environment variables:
  HELM_WORKSPACE  Override the default workspace path (~/.helm/workspace)
  HELM_REPO_URL   Override the default git repository URL
  HELM_SKIP_INIT  Set to 1 to skip workspace initialization
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found." >&2
  exit 1
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "python3 -m pip is required but was not found." >&2
  exit 1
fi

echo "Installing Helm from ${REPO_URL}"
python3 -m pip install --user --no-build-isolation "git+${REPO_URL}"

PYTHON_USER_BIN="$(python3 -m site --user-base)/bin"
HELM_BIN="${PYTHON_USER_BIN}/helm"

if [ ! -x "${HELM_BIN}" ]; then
  echo "Helm binary was not found at ${HELM_BIN} after install." >&2
  exit 1
fi

if [ "${SKIP_INIT}" != "1" ]; then
  echo "Initializing Helm workspace at ${WORKSPACE_PATH}"
  "${HELM_BIN}" init --path "${WORKSPACE_PATH}" >/dev/null
fi

case ":$PATH:" in
  *":${PYTHON_USER_BIN}:"*) ;;
  *)
    echo
    echo "Add this to your shell profile if \`helm\` is not found:"
    echo "  export PATH=\"${PYTHON_USER_BIN}:\$PATH\""
    ;;
esac

echo
echo "Helm installed."
echo "Binary: ${HELM_BIN}"
if [ "${SKIP_INIT}" != "1" ]; then
  echo "Workspace: ${WORKSPACE_PATH}"
fi
echo
echo "Try:"
echo "  ${HELM_BIN} --help"
if [ "${SKIP_INIT}" != "1" ]; then
  echo "  ${HELM_BIN} survey --path \"${WORKSPACE_PATH}\""
fi
