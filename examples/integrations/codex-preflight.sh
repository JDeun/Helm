#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${HELM_WORKSPACE:-$HOME/.helm/workspace}"
PROJECT="${1:-$PWD}"

helm profile --path "$WORKSPACE" run inspect_local \
  --task-name "Codex preflight inspection" \
  -- git -C "$PROJECT" status --short
