"""Configuration management for Geo Tracker.

Loads settings from environment variables with sensible defaults.
Supports dev/staging/prod environments via FLASK_ENV.
"""

import os
from pathlib import Path


class Config:
    """Base configuration."""

    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = FLASK_ENV == "development"

    # Server
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", 5000))

    # Database
    BASE_DIR = Path(__file__).parent
    DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "geo.db"))

    # Session settings
    SESSION_STALE_AFTER = int(os.environ.get("SESSION_STALE_AFTER", 120))  # seconds
    SESSION_AUTO_DELETE_HOURS = int(os.environ.get("SESSION_AUTO_DELETE_HOURS", 0))  # 0 = disabled

    # Rate limiting
    RATELIMIT_ENABLED = os.environ.get("RATELIMIT_ENABLED", "true").lower() == "true"
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "100/hour")

    # CORS (for development)
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")

    @classmethod
    def get_config(cls):
        """Return appropriate config based on FLASK_ENV."""
        env = os.environ.get("FLASK_ENV", "development")
        if env == "production":
            return ProductionConfig
        elif env == "testing":
            return TestingConfig
        return DevelopmentConfig


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    SESSION_AUTO_DELETE_HOURS = 24  # Auto-delete after 24 hours in dev


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DB_PATH = ":memory:"  # Use in-memory database for tests


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    SESSION_STALE_AFTER = 60
    SESSION_AUTO_DELETE_HOURS = 168  # 7 days


def load_config():
    """Load configuration based on environment."""
    return Config.get_config()()
