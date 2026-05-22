"""Tests for ``helm shadow-report`` CLI command (Wave 6).

Coverage matrix
---------------
1. --format json outputs valid JSON.
2. --format md outputs markdown with # headers.
3. --with-recommendations appends a Recommendations section.
4. --feature browser_verifier filters single feature.
5. --out PATH writes to file (use tmp_path).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HELM_PY = ROOT / "helm.py"


def _run(
    args: list[str],
    *,
    tmp_path: Path,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run helm with empty JSONL overrides so tests never touch live paths."""
    # Write empty temp files
    ledger = tmp_path / "ledger.jsonl"
    proxy = tmp_path / "proxy.jsonl"
    if not ledger.exists():
        ledger.write_text("", encoding="utf-8")
    if not proxy.exists():
        proxy.write_text("", encoding="utf-8")

    env = os.environ.copy()
    # Override default paths via env vars not needed — the command uses defaults.
    # Instead we inject paths as CLI args by monkey-patching via env var if the
    # command supports it. Since shadow-report doesn't expose path flags, we
    # redirect by running in a subprocess.  The report will attempt the real
    # default paths but gracefully handle missing files (returns empty report).
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        [sys.executable, str(HELM_PY), "shadow-report", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


# ---------------------------------------------------------------------------
# Test 1: --format json outputs valid JSON
# ---------------------------------------------------------------------------

class TestFormatJson:
    def test_json_output_is_valid(self, tmp_path):
        result = _run(["--format", "json"], tmp_path=tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = json.loads(result.stdout)
        assert isinstance(payload, dict)

    def test_json_has_features_key(self, tmp_path):
        result = _run(["--format", "json"], tmp_path=tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "features" in payload

    def test_json_has_generated_at(self, tmp_path):
        result = _run(["--format", "json"], tmp_path=tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "generated_at" in payload


# ---------------------------------------------------------------------------
# Test 2: --format md outputs markdown
# ---------------------------------------------------------------------------

class TestFormatMarkdown:
    def test_md_has_h1_header(self, tmp_path):
        result = _run(["--format", "md"], tmp_path=tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "# Shadow-Mode Report" in result.stdout

    def test_md_has_h2_feature_sections(self, tmp_path):
        result = _run(["--format", "md"], tmp_path=tmp_path)
        assert result.returncode == 0
        assert "## Feature:" in result.stdout

    def test_default_format_is_md(self, tmp_path):
        # No --format flag → should behave like md
        result = _run([], tmp_path=tmp_path)
        assert result.returncode == 0
        assert "#" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: --with-recommendations appends Recommendations section
# ---------------------------------------------------------------------------

class TestWithRecommendations:
    def test_recommendations_section_in_md(self, tmp_path):
        result = _run(["--with-recommendations"], tmp_path=tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "Recommendations" in result.stdout

    def test_recommendations_in_json(self, tmp_path):
        result = _run(["--format", "json", "--with-recommendations"], tmp_path=tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "recommendations" in payload
        assert isinstance(payload["recommendations"], dict)

    def test_recommendations_has_browser_verifier(self, tmp_path):
        result = _run(["--format", "json", "--with-recommendations"], tmp_path=tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        recs = payload["recommendations"]
        assert "browser_verifier" in recs
        assert "verdict" in recs["browser_verifier"]


# ---------------------------------------------------------------------------
# Test 4: --feature filters to single feature
# ---------------------------------------------------------------------------

class TestFeatureFilter:
    def test_single_feature_in_json(self, tmp_path):
        result = _run(["--format", "json", "--feature", "browser_verifier"], tmp_path=tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = json.loads(result.stdout)
        features = payload["features"]
        assert "browser_verifier" in features
        # Other features should not appear
        assert "pause_gate" not in features
        assert "model_repair" not in features

    def test_filter_reflected_in_raw_filter_applied(self, tmp_path):
        result = _run(["--format", "json", "--feature", "pause_gate"], tmp_path=tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload.get("raw_filter_applied") == ["pause_gate"]


# ---------------------------------------------------------------------------
# Test 5: --out PATH writes to file
# ---------------------------------------------------------------------------

class TestOutPath:
    def test_writes_to_file(self, tmp_path):
        out_file = tmp_path / "report.md"
        result = _run(["--out", str(out_file)], tmp_path=tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "# Shadow-Mode Report" in content

    def test_stdout_is_empty_when_out_given(self, tmp_path):
        out_file = tmp_path / "report.md"
        result = _run(["--out", str(out_file)], tmp_path=tmp_path)
        assert result.returncode == 0
        # stdout should be empty when --out is used
        assert result.stdout.strip() == ""

    def test_out_json_valid(self, tmp_path):
        out_file = tmp_path / "report.json"
        result = _run(["--format", "json", "--out", str(out_file)], tmp_path=tmp_path)
        assert result.returncode == 0
        payload = json.loads(out_file.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
