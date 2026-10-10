"""Tests for the command-line options exposed by the entry point."""

import inspect

import pytest

from main import build_arg_parser
from ui.tables import TableManager


def test_progressive_flags_are_gone():
    """The dead progressive-loading flags are rejected and nothing reads them."""
    parser = build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--progressive-load"])

    with pytest.raises(SystemExit):
        parser.parse_args(["--progressive-chunk-size", "30"])

    assert "progressive" not in inspect.signature(TableManager.populate).parameters
