"""Shared fixtures.

Tests construct the source explicitly rather than relying on a default inside
the pipeline. That is the point of the `GuestSource` seam: what a test depends
on should be visible in the test.
"""

import pytest

from gncl.load import load_all
from gncl.match import build as build_match
from gncl.output import build_guests
from gncl.ports import CsvSource


@pytest.fixture(scope="session")
def source():
    return CsvSource()


@pytest.fixture(scope="session")
def frames(source):
    return source.frames()


@pytest.fixture(scope="session")
def match_result(frames):
    return build_match(frames)


@pytest.fixture(scope="session")
def sample_guests(match_result, frames):
    return build_guests(match_result, frames)


__all__ = ["build_guests", "build_match", "load_all"]
