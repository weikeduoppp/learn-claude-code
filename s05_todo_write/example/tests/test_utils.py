"""Tests for demo_pkg.utils."""

from demo_pkg.utils import add


def test_add() -> None:
    """add returns the sum of two integers."""
    assert add(2, 3) == 5
