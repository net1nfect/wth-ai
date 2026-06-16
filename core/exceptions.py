"""Domain-specific exceptions used across H1T4M-AI."""


class H1T4MError(Exception):
    """Base exception for the framework."""


class ConfigurationError(H1T4MError):
    """Raised for invalid configuration."""


class TargetError(H1T4MError):
    """Raised for invalid target definitions."""


class AuthorizationError(H1T4MError):
    """Raised when an operation violates authorization or scope."""


class ToolError(H1T4MError):
    """Raised when a managed tool fails."""


class NetworkError(H1T4MError):
    """Raised for network operation failures."""


class ExploitationError(H1T4MError):
    """Raised for controlled validation failures."""


class DataError(H1T4MError):
    """Raised for data processing failures."""


class PersistenceError(H1T4MError):
    """Raised for persistence layer failures."""


class LearningError(H1T4MError):
    """Raised for learning subsystem failures."""


class ReportingError(H1T4MError):
    """Raised for report generation failures."""
