from __future__ import annotations

from uuid import uuid4


SCHEMA_VERSION = 1


def success(data: object) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": str(uuid4()),
        "data": data,
        "error": None,
    }


def failure(code: str, message: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": str(uuid4()),
        "data": None,
        "error": {"code": code, "message": message},
    }
