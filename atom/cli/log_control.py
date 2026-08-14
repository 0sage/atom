"""Runtime log visibility controls shared by CLI commands."""

from loguru import logger

__all__ = ["_set_atom_logs"]


def _set_atom_logs(enabled: bool) -> None:
    if enabled:
        logger.enable("atom")
    else:
        logger.disable("atom")
