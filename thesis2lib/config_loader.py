"""Load static run configuration from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

FORBIDDEN_CONFIG_KEYS = frozenset(
    {
        "token",
        "canvas_token",
        "api_key",
        "password",
        "secret",
        "authorization",
        "bearer",
    }
)

PATH_FIELDS = (
    "first_pdf_folder",
    "second_pdf_folder",
    "output_folder",
    "overview_file",
    "assessment_file",
)

CONFIG_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "canvas_url": ("canvas_url", "canvas.url"),
    "course_id": ("course_id", "canvas.course_id"),
    "first_assignment_id": (
        "first_assignment_id",
        "canvas.first_assignment_id",
        "first_submission_assignment_id",
    ),
    "second_assignment_id": (
        "second_assignment_id",
        "canvas.second_assignment_id",
        "second_submission_assignment_id",
    ),
    "programme": ("programme", "program"),
    "semester": ("semester",),
    "pass_threshold": ("pass_threshold",),
    "download_workers": ("download_workers",),
    "title_workers": ("title_workers",),
    "first_pdf_folder": ("first_pdf_folder", "paths.first_pdf_folder"),
    "second_pdf_folder": ("second_pdf_folder", "paths.second_pdf_folder"),
    "output_folder": ("output_folder", "paths.output_folder"),
    "overview_file": ("overview_file", "paths.overview_file"),
    "assessment_file": ("assessment_file", "paths.assessment_file"),
}


def _lookup_nested(config: dict[str, Any], dotted_key: str) -> Any:
    current: Any = config
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _flatten_keys(config: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        keys.add(full_key.casefold())
        if isinstance(value, dict):
            keys.update(_flatten_keys(value, full_key))
    return keys


def _reject_forbidden_keys(config: dict[str, Any]) -> None:
    for key in _flatten_keys(config):
        for forbidden in FORBIDDEN_CONFIG_KEYS:
            if forbidden in key:
                raise ValueError(
                    "Config file must not contain secrets such as tokens or passwords."
                )


def load_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML mapping: {path}")

    _reject_forbidden_keys(raw)

    defaults: dict[str, Any] = {}
    for field_name, aliases in CONFIG_FIELD_ALIASES.items():
        for alias in aliases:
            if "." in alias:
                value = _lookup_nested(raw, alias)
            else:
                value = raw.get(alias)
            if value is None:
                continue
            if field_name in PATH_FIELDS and value:
                defaults[field_name] = Path(str(value))
            else:
                defaults[field_name] = value
            break

    return defaults


def load_config_defaults(config_path: Path | None) -> dict[str, Any]:
    if config_path is None or not config_path.is_file():
        return {}
    return load_config_file(config_path)


def validate_required_settings(args: Any) -> None:
    required = (
        "canvas_url",
        "course_id",
        "first_assignment_id",
        "second_assignment_id",
        "output_folder",
        "programme",
        "semester",
    )
    missing = [name for name in required if getattr(args, name, None) in (None, "")]
    if missing:
        raise ValueError(
            "Missing required settings: "
            + ", ".join(missing)
            + ". Provide them in config.yml or on the command line."
        )
