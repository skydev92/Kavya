"""
Constants for Kavya application.
Centralizes all magic strings to eliminate hardcoded values.
"""

import os


class AgentType:
    """Agent type constants."""

    WEB_SEARCH = "web_search"
    CONTENT_WRITER = "content_writer"
    ROUTER = "router"


class ObservationName:
    """Observation name constants for Langfuse hierarchy."""

    WEB_SEARCH = "web_search"
    LONGWRITER = "longwriter"
    ROUTER = "router"


class Environment:
    """Environment constants."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    @classmethod
    def get_current(cls) -> str:
        """Get current environment with fallback to development."""
        return os.getenv("ENVIRONMENT", cls.DEVELOPMENT)


class TagPrefix:
    """Tag prefix constants for consistent tagging."""

    AGENT = "agent"
    CONTROLLER = "controller"
    MODEL = "model"
    ENV = "env"
    STEP = "step"
    RETRY = "retry"

    # Common tag values
    FALLBACK = "fallback"
    SYNC_COMPLETION = "sync_completion"
    STREAMING = "streaming"
    LONGWRITER = "longwriter"
    ROUTING = "routing"
