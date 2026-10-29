"""Structured error types. Messages must never contain secret values."""

from __future__ import annotations


class Mt5BotError(Exception):
    """Base class. ``code`` is a stable machine-readable reason code."""

    code = "MT5BOT_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ConfigError(Mt5BotError):
    code = "CONFIG_INVALID"


class GatewayError(Mt5BotError):
    code = "GATEWAY_ERROR"


class TerminalUnavailableError(GatewayError):
    code = "TERMINAL_UNAVAILABLE"


class AccountValidationError(GatewayError):
    code = "ACCOUNT_INVALID"


class SymbolResolutionError(GatewayError):
    code = "SYMBOL_UNRESOLVED"


class AmbiguousSymbolError(SymbolResolutionError):
    code = "SYMBOL_AMBIGUOUS"


class DataQualityError(Mt5BotError):
    code = "DATA_QUALITY"


class StorageError(Mt5BotError):
    code = "STORAGE_ERROR"


class ModelUnavailableError(Mt5BotError):
    code = "MODEL_UNAVAILABLE"


class ModelIntegrityError(ModelUnavailableError):
    code = "MODEL_INTEGRITY"


class ProviderError(Mt5BotError):
    code = "NEWS_PROVIDER_ERROR"


class ExecutionDisabledError(Mt5BotError):
    """Raised whenever anything attempts to reach a trading function."""

    code = "EXECUTION_DISABLED"
