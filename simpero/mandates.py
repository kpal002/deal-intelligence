"""Mandate loading: parse a YAML rubric into a validated :class:`Mandate`.

Mandates are authored as data (YAML) and validated through Pydantic on load, so
a malformed rubric fails immediately with a clear error rather than at scoring
time. The same loader is used by the demo seed and the API.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from simpero.models.mandate import Mandate

logger = logging.getLogger(__name__)


def load_mandate_from_yaml(path: str | Path) -> Mandate:
    """Load and validate a mandate from a YAML file.

    Args:
        path: Path to the mandate YAML.

    Returns:
        A validated :class:`Mandate`.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the YAML is malformed or fails mandate validation.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Mandate file not found: {file_path}")
    try:
        raw = yaml.safe_load(file_path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {file_path}: {exc}") from exc
    try:
        mandate = Mandate.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"Mandate in {file_path} failed validation: {exc}") from exc
    logger.info(
        "Loaded mandate '%s' v%d with %d criteria",
        mandate.fund_name,
        mandate.mandate_version,
        len(mandate.criteria),
    )
    return mandate
