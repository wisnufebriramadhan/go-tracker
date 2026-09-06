"""Custom exceptions for Geo Tracker application."""


class GeoTrackerError(Exception):
    """Base exception for all Geo Tracker errors."""
    pass


class DatabaseError(GeoTrackerError):
    """Database operation errors."""
    pass


class SessionNotFoundError(DatabaseError):
    """Raised when a session is not found."""
    pass


class SessionExpiredError(DatabaseError):
    """Raised when a session has expired."""
    pass


class SessionPausedError(DatabaseError):
    """Raised when trying to update a paused session."""
    pass


class InvalidLocationError(GeoTrackerError):
    """Raised when location data is invalid."""
    pass


class ValidationError(GeoTrackerError):
    """Raised when input validation fails."""
    pass


class APIError(GeoTrackerError):
    """Raised when external API calls fail."""
    pass


class IPLookupError(APIError):
    """Raised when IP lookup fails."""
    pass


class PhoneLookupError(APIError):
    """Raised when phone lookup fails."""
    pass


class RateLimitError(GeoTrackerError):
    """Raised when rate limit is exceeded."""
    pass


class SecurityError(GeoTrackerError):
    """Raised for security-related errors."""
    pass


class CSRFError(SecurityError):
    """Raised for CSRF validation errors."""
    pass
