# Helm 0.3.0 Release Checklist

Use this checklist before cutting the `0.3.0` release.

## Version and metadata

- confirm `pyproject.toml` version is `0.3.0`
- confirm `setup.py` version is `0.3.0`
- confirm `CHANGELOG.md` includes `0.3.0`
- confirm README asset links and docs links render correctly

## Packaging

- verify `install.sh` syntax with `bash -n install.sh`
- verify `python3 -m pip install --user --no-build-isolation .` succeeds
- verify installed `helm --help` works
- verify `helm survey` appears in help output
- verify `helm memory` appears in help output
- verify `python3 scripts/run_with_profile.py validate-manifests --json` reports `ok: true`

## Workspace and onboarding smoke tests

- `helm init --path /tmp/helm-release-smoke`
- `helm survey --path /tmp/helm-release-smoke`
- `helm onboard --path /tmp/helm-release-smoke --adopt-openclaw ~/.openclaw/workspace`
- confirm `helm sources --path /tmp/helm-release-smoke`
- `helm doctor --path /tmp/helm-release-smoke`
- `helm validate --path /tmp/helm-release-smoke`
- `helm status --path /tmp/helm-release-smoke --verbose`
- `helm ops --path /tmp/helm-release-smoke capture-state`
- `python3 scripts/run_with_profile.py validate-manifests --json`

## Demo workspace smoke tests

- `helm survey --path examples/demo-workspace`
- `helm doctor --path examples/demo-workspace`
- `helm validate --path examples/demo-workspace`
- `python3 scripts/run_with_profile.py validate-manifests --json`
- `helm context --path examples/demo-workspace --include notes tasks commands --summary --limit 8`
- `helm context --path examples/demo-workspace recent-state --limit 5`
- `helm memory --path examples/demo-workspace pending-captures --limit 5`
- `helm ops --path examples/demo-workspace capture-state --limit 10`
- `helm checkpoint --path examples/demo-workspace finalize`
- `helm checkpoint recommend --path examples/demo-workspace`
- `helm report --path examples/demo-workspace --format markdown`

## Release outputs

- create git tag: `v0.3.0`
- draft GitHub release notes from `docs/releases/0.3.0.md`
- attach screenshots or README visuals if needed
- publish source release after the checklist passes
