"""Stable API errors shared by gateway routes and services."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def unauthorized(message: str = "Authentication required") -> AppError:
    return AppError(401, "UNAUTHORIZED", message)


def forbidden(message: str = "Forbidden") -> AppError:
    return AppError(403, "FORBIDDEN", message)


def not_found(message: str = "Not found") -> AppError:
    return AppError(404, "NOT_FOUND", message)


def conflict(message: str) -> AppError:
    return AppError(409, "CONFLICT", message)


def validation(message: str, details: Any = None) -> AppError:
    return AppError(400, "VALIDATION_ERROR", message, details)


def unavailable(message: str) -> AppError:
    return AppError(503, "UPSTREAM_UNAVAILABLE", message)


def is_definitive_rejection(error: Exception) -> bool:
    status = error.status_code if isinstance(error, AppError) else getattr(error, "status", None)
    return isinstance(status, int) and 400 <= status < 500
