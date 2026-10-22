"""
Tests for CLI argument parsing and validation.
"""
import subprocess
import sys
from pathlib import Path

import pytest

# Path to the CLI module entry-point.
CLI = [sys.executable, "-m", "src.cli"]
ROOT = Path(__file__).parent.parent


def run_cli(*args, cwd=None, env=None):
    """Run the CLI in a subprocess and return (returncode, stdout, stderr)."""
    import os
    env_vars = os.environ.copy()
    if env:
        env_vars.update(env)
    result = subprocess.run(
        [sys.executable, "-m", "src.cli"] + list(args),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env_vars,
    )
    return result.returncode, result.stdout, result.stderr


class TestHelpAndBasicArgs:
    def test_help_exits_zero(self):
        """--help should print usage and exit 0."""
        rc, stdout, _ = run_cli("--help")
        assert rc == 0
        assert "--input" in stdout
        assert "--targets" in stdout
        assert "--out-dir" in stdout

    def test_missing_input_exits_nonzero(self):
        """Missing --input must exit with a non-zero code."""
        rc, _, stderr = run_cli("--targets", "es")
        assert rc != 0
        assert "input" in stderr.lower() or "required" in stderr.lower()

    def test_missing_targets_exits_nonzero(self):
        """Missing --targets must exit with a non-zero code."""
        rc, _, stderr = run_cli("--input", "test.wav")
        assert rc != 0
        assert "targets" in stderr.lower() or "required" in stderr.lower()


class TestParseTargets:
    """Unit-level tests for the parse_targets helper."""

    def _import_parse_targets(self):
        import importlib
        module = importlib.import_module("src.cli")
        return module.parse_targets

    def test_single_supported_code(self):
        parse_targets = self._import_parse_targets()
        result = parse_targets("es")
        assert result == ["es"]

    def test_multiple_supported_codes(self):
        parse_targets = self._import_parse_targets()
        result = parse_targets("es,fr,de")
        assert result == ["es", "fr", "de"]

    def test_duplicates_are_removed(self):
        parse_targets = self._import_parse_targets()
        result = parse_targets("es,fr,es")
        assert result == ["es", "fr"]

    def test_unsupported_code_raises(self):
        parse_targets = self._import_parse_targets()
        with pytest.raises(ValueError, match="Unsupported"):
            parse_targets("xx")

    def test_empty_string_raises(self):
        import argparse
        parse_targets = self._import_parse_targets()
        with pytest.raises((ValueError, SystemExit, argparse.ArgumentTypeError)):
            parse_targets("")
