# Helm 0.1.0 Release Checklist

Use this checklist before cutting the `0.1.0` release.

## Version and metadata

- confirm `pyproject.toml` version is `0.1.0`
- confirm `setup.py` version is `0.1.0`
- confirm `CHANGELOG.md` includes `0.1.0`
- confirm README asset links and docs links render correctly

## Packaging

- verify `install.sh` syntax with `bash -n install.sh`
- verify `python3 -m pip install --user --no-build-isolation .` succeeds
- verify installed `helm --help` works
- verify `helm survey` appears in help output

## Workspace and onboarding smoke tests

- `helm init --path /tmp/helm-release-smoke`
- `helm survey --path /tmp/helm-release-smoke`
- `helm onboard --path /tmp/helm-release-smoke --adopt-openclaw ~/.openclaw/workspace`
- confirm `helm sources --path /tmp/helm-release-smoke`
- `helm doctor --path /tmp/helm-release-smoke`
- `helm validate --path /tmp/helm-release-smoke`
- `helm status --path /tmp/helm-release-smoke --verbose`

## Demo workspace smoke tests

- `helm survey --path examples/demo-workspace`
- `helm doctor --path examples/demo-workspace`
- `helm validate --path examples/demo-workspace`
- `helm context --path examples/demo-workspace --include notes tasks commands --summary --limit 8`
- `helm checkpoint recommend --path examples/demo-workspace`
- `helm report --path examples/demo-workspace --format markdown`

## Release outputs

- create git tag: `v0.1.0`
- draft GitHub release notes from `docs/releases/0.1.0.md`
- attach screenshots or README visuals if needed
- publish source release after the checklist passes
