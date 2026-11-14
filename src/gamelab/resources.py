"""The package's own blueprint (family descriptors, their schema, prompts) and schema checks."""
from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

from jsonschema import Draft202012Validator

FRAGMENT: Traversable = files(__package__) / "blueprint"


def validate(value: Any, schema: dict[str, Any]) -> list[str]:
    """What in `value` breaks `schema`, as readable lines; empty when it fits."""
    return [f"{'/'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
            for error in Draft202012Validator(schema).iter_errors(value)]
