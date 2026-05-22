"""Tests for scripts/env_flags.py — shared truthy detection."""
from __future__ import annotations

import pytest

from scripts.env_flags import env_flag, is_truthy


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", "Yes", " 1 ", "  TRUE  "])
def test_is_truthy_accepts_documented_values(value):
    assert is_truthy(value) is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "x", " ", "01", "true.", "2"])
def test_is_truthy_rejects_other_values(value):
    assert is_truthy(value) is False


def test_env_flag_reads_environment(monkeypatch):
    monkeypatch.setenv("HELM_TEST_FLAG", "yes")
    assert env_flag("HELM_TEST_FLAG") is True
    monkeypatch.setenv("HELM_TEST_FLAG", "no")
    assert env_flag("HELM_TEST_FLAG") is False


def test_env_flag_unset_is_false(monkeypatch):
    monkeypatch.delenv("HELM_TEST_FLAG_UNSET", raising=False)
    assert env_flag("HELM_TEST_FLAG_UNSET") is False
