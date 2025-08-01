"""
Comprehensive Langfuse integration manager.
Centralizes all observability logic to eliminate DRY violations.
"""

import logging
import os
import uuid
from contextlib import contextmanager
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentType(Enum):
    """Centralized agent type definitions to eliminate magic strings."""

    WEB_SEARCH = "web_search"
    CONTENT_WRITER = "content_writer"
    ROUTER = "router"


class LangfuseManager:
    """
    Centralized Langfuse integration manager.
    Eliminates DRY violations and provides consistent interface.
    """

    def __init__(self):
        self.active_observations: Dict[str, str] = {}

    def _generate_observation_id(self, feature_name: str) -> str:
        """Generate a unique observation ID."""
        return f"obs-{feature_name}-{uuid.uuid4().hex[:8]}"

    def _get_base_metadata(
        self,
        user_id: str,
        controller_class: str,
        model_requested: str,
        model_actual: str,
        additional_fields: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Generate base metadata that's common to all completions."""
        metadata = {
            "trace_user_id": user_id,
            "controller_type": controller_class,
            "model_requested": model_requested,
            "model_actual": model_actual,
        }
        if additional_fields:
            metadata.update(additional_fields)
        return metadata

    def _get_base_tags(
        self,
        controller_class: str,
        model: str,
        additional_tags: Optional[List[str]] = None,
    ) -> List[str]:
        """Generate base tags that are common to all completions."""
        tags = [
            f"controller:{controller_class}",
            f"model:{model}",
            f"env:{os.getenv('ENVIRONMENT', 'development')}",
        ]
        if additional_tags:
            tags.extend(additional_tags)
        return tags

    def create_controller_metadata(
        self,
        user_id: str,
        controller_class: str,
        model_requested: str,
        model_actual: str,
        is_sync: bool = False,
        is_retry: bool = False,
        is_fallback: bool = False,
        retry_attempt: int = 1,
        additional_metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Create standardized metadata for controller completion calls."""

        # Base metadata
        metadata = self._get_base_metadata(
            user_id,
            controller_class,
            model_requested,
            model_actual,
            additional_metadata,
        )

        # Base tags
        tags = self._get_base_tags(controller_class, model_actual)

        # Add specific tags
        if is_sync:
            tags.append("sync_completion")
        if is_retry:
            tags.append(f"retry:{retry_attempt}")
        if is_fallback:
            tags.append("fallback")

        metadata["tags"] = tags
        return metadata

    def create_agent_metadata(
        self,
        agent_type: AgentType,
        step: str,
        generation_name: str,
        user_id: str,
        parent_observation_id: Optional[str] = None,
        trace_name: Optional[str] = None,
        additional_metadata: Optional[Dict] = None,
        additional_tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create standardized metadata for agent completion calls."""

        metadata = {
            "trace_user_id": user_id,
            "generation_name": generation_name,
            "agent_type": agent_type.value,
            "step": step,
        }

        # Add parent observation ID if provided
        if parent_observation_id:
            metadata["parent_observation_id"] = parent_observation_id

        # Add trace name for root observations
        if trace_name:
            metadata["trace_name"] = trace_name

        # Add additional metadata
        if additional_metadata:
            metadata.update(additional_metadata)

        # Create tags
        tags = [f"agent:{agent_type.value}", f"step:{step}"]
        if additional_tags:
            tags.extend(additional_tags)

        metadata["tags"] = tags
        return metadata

    @contextmanager
    def observation_scope(self, feature_name: str):
        """Context manager for observation lifecycle."""
        obs_id = self._generate_observation_id(feature_name)
        self.active_observations[feature_name] = obs_id

        logging.info(f"{feature_name.upper()}: Created observation {obs_id}")

        try:
            yield obs_id
        finally:
            if feature_name in self.active_observations:
                del self.active_observations[feature_name]
            logging.info(f"{feature_name.upper()}: Cleared observation {obs_id}")

    def get_observation_id(self, feature_name: str) -> Optional[str]:
        """Get active observation ID for a feature."""
        return self.active_observations.get(feature_name)


# Global instance
langfuse_manager = LangfuseManager()


# Convenience functions for backward compatibility
def create_langfuse_metadata(
    agent_type: str,
    step: str,
    generation_name: str,
    user_id: Optional[str] = None,
    parent_observation_id: Optional[str] = None,
    trace_name: Optional[str] = None,
    additional_metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[list] = None,
) -> Dict[str, Any]:
    """Backward compatibility wrapper - DEPRECATED."""
    logging.warning(
        "create_langfuse_metadata is deprecated. Use LangfuseManager methods."
    )

    # Convert string to enum
    try:
        agent_enum = AgentType(agent_type)
    except ValueError:
        # Fallback for unknown agent types
        agent_enum = AgentType.CONTENT_WRITER

    return langfuse_manager.create_agent_metadata(
        agent_type=agent_enum,
        step=step,
        generation_name=generation_name,
        user_id=user_id,
        parent_observation_id=parent_observation_id,
        trace_name=trace_name,
        additional_metadata=additional_metadata,
        additional_tags=tags,
    )
