"""Helper functions for managing Langfuse observation hierarchy."""

import logging
import uuid
from typing import Any, Dict, Optional

from kavya.constants import Environment, TagPrefix


class LangfuseObservationManager:
    """Manages observation IDs for hierarchical tracking in Langfuse."""

    def __init__(self):
        self.active_observations: Dict[str, str] = {}

    def create_observation_id(self, observation_name: str) -> str:
        """Create a new observation ID for a feature."""
        obs_id = f"obs-{observation_name}-{uuid.uuid4().hex[:8]}"
        self.active_observations[observation_name] = obs_id
        logging.info(f"{observation_name.upper()}: Created observation {obs_id}")
        return obs_id

    def get_observation_id(self, feature_name: str) -> Optional[str]:
        """Get the active observation ID for a feature."""
        return self.active_observations.get(feature_name)

    def clear_observation(self, observation_name: str):
        """Clear the observation ID for a feature."""
        if observation_name in self.active_observations:
            del self.active_observations[observation_name]
            logging.info(f"{observation_name.upper()}: Cleared observation")


# Global instance for the application
observation_manager = LangfuseObservationManager()


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
    """Create standardized Langfuse metadata for a completion call."""
    metadata = {
        "trace_user_id": user_id,
        "generation_name": generation_name,
        "agent_type": agent_type,
        "step": step,
    }

    # Add parent observation ID if provided
    if parent_observation_id:
        metadata["parent_observation_id"] = parent_observation_id

    # Add trace name for root observations
    if trace_name:
        metadata["trace_name"] = trace_name

    # Add any additional metadata
    if additional_metadata:
        metadata.update(additional_metadata)

    # Prepare base tags using constants
    base_tags = [
        f"{TagPrefix.AGENT}:{agent_type}",
        f"{TagPrefix.STEP}:{step}",
        f"{TagPrefix.ENV}:{Environment.get_current()}",
    ]

    # Add custom tags
    if tags:
        base_tags.extend(tags)

    metadata["tags"] = base_tags
    return metadata


def create_controller_metadata(
    user_id: str,
    controller_class: str,
    model_requested: str,
    model_actual: str,
    is_sync: bool = False,
    is_retry: bool = False,
    is_fallback: bool = False,
    retry_attempt: int = 1,
    additional_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create standardized metadata for controller completion calls."""

    metadata = {
        "trace_user_id": user_id,
        "controller_type": controller_class,
        "model_requested": model_requested,
        "model_actual": model_actual,
    }

    # Add additional metadata
    if additional_metadata:
        metadata.update(additional_metadata)

    # Create base tags using constants
    tags = [
        f"{TagPrefix.CONTROLLER}:{controller_class}",
        f"{TagPrefix.MODEL}:{model_actual}",
        f"{TagPrefix.ENV}:{Environment.get_current()}",
    ]

    # Add conditional tags
    if is_sync:
        tags.append(TagPrefix.SYNC_COMPLETION)
    if is_retry:
        tags.append(f"{TagPrefix.RETRY}:{retry_attempt}")
    if is_fallback:
        tags.append(TagPrefix.FALLBACK)

    metadata["tags"] = tags
    return metadata
