"""Execute generated Python with an explicit, detached namespace."""

from __future__ import annotations

import builtins
import json
import keyword
import math
from collections.abc import Iterable
from typing import cast

from .json_types import JsonValue


class SafeExecError(ValueError):
    """Raised when the explicit input or result boundary is invalid."""


_RESERVED_VARIABLE_NAMES = frozenset({"result"})


def safe_exec(code: str, variables: dict[str, JsonValue] | None = None) -> JsonValue:
    """Execute generated Python and return its JSON-compatible ``result`` value."""

    source = _validate_code(code)
    namespace = _new_namespace(_detach_variables(variables))
    exec(source, namespace, namespace)  # noqa: S102 - execution is this helper's contract
    if "result" not in namespace:
        raise SafeExecError("code must assign result")
    return _detach_json_value(namespace["result"], boundary="result")


def _validate_code(code: object) -> str:
    if type(code) is not str:
        raise SafeExecError("code must be a string")
    return code


def _detach_variables(variables: object) -> dict[str, JsonValue]:
    if variables is None:
        return {}
    if type(variables) is not dict:
        raise SafeExecError("variables must be an exact dict")

    mapping = cast(dict[object, object], variables)
    for name in mapping:
        if type(name) is not str or not name.isidentifier() or keyword.iskeyword(name):
            raise SafeExecError("variable names must be ordinary Python identifiers")
        if name.startswith("__") or name in _RESERVED_VARIABLE_NAMES:
            raise SafeExecError("variable name is reserved by safe_exec")

    detached = _detach_json_value(mapping, boundary="variables")
    return cast(dict[str, JsonValue], detached)


def _new_namespace(variables: dict[str, JsonValue]) -> dict[str, object]:
    namespace: dict[str, object] = {
        "__builtins__": dict(vars(builtins)),
        "__name__": "__safe_exec__",
        "__package__": None,
    }
    namespace.update(variables)
    return namespace


def _detach_json_value(value: object, *, boundary: str) -> JsonValue:
    _validate_exact_json_value(value, active_containers=set(), boundary=boundary)
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
        return cast(JsonValue, json.loads(encoded))
    except (RecursionError, TypeError, ValueError, OverflowError):
        raise SafeExecError(f"{boundary} must be JSON-compatible") from None


def _validate_exact_json_value(
    value: object,
    *,
    active_containers: set[int],
    boundary: str,
) -> None:
    value_type = type(value)
    if value is None or value_type in {bool, int, str}:
        return
    if value_type is float:
        if not math.isfinite(cast(float, value)):
            raise SafeExecError(f"{boundary} must be JSON-compatible")
        return
    if value_type is list:
        _validate_json_container(
            value,
            cast(list[object], value),
            active_containers=active_containers,
            boundary=boundary,
        )
        return
    if value_type is dict:
        mapping = cast(dict[object, object], value)
        if any(type(key) is not str for key in mapping):
            raise SafeExecError(f"{boundary} must be JSON-compatible")
        _validate_json_container(
            value,
            mapping.values(),
            active_containers=active_containers,
            boundary=boundary,
        )
        return
    raise SafeExecError(f"{boundary} must be JSON-compatible")


def _validate_json_container(
    container: object,
    values: Iterable[object],
    *,
    active_containers: set[int],
    boundary: str,
) -> None:
    identity = id(container)
    if identity in active_containers:
        raise SafeExecError(f"{boundary} must be JSON-compatible")
    active_containers.add(identity)
    try:
        for value in values:
            _validate_exact_json_value(
                value,
                active_containers=active_containers,
                boundary=boundary,
            )
    finally:
        active_containers.remove(identity)


__all__ = ["SafeExecError", "safe_exec"]
