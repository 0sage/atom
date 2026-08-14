"""Cross-suite test infrastructure."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def _isolate_atom_log_activation() -> Iterator[None]:
    """Keep CLI log settings from leaking into later tests in the same process."""
    logger.enable("atom")
    try:
        yield
    finally:
        logger.enable("atom")
