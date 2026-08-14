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


@pytest.fixture(autouse=True)
def _isolate_secret_store(tmp_path_factory, monkeypatch) -> Iterator[None]:
    """Point the default secret store at a throwaway file for every test.

    ``ExecTool._build_env`` reads the store, so any test that builds a subprocess
    environment would otherwise read — and a mistakenly un-injected write would
    modify — the developer's real ``~/.atom/private/secrets.env``. Isolating
    globally means forgetting to inject a store fails harmlessly rather than
    silently touching real secrets.
    """
    from atom.privacy import store as store_module

    isolated = tmp_path_factory.mktemp("secret-store") / "secrets.env"
    monkeypatch.setattr(
        store_module, "DEFAULT_SECRET_STORE", store_module.SecretStore(path=isolated)
    )
    yield
