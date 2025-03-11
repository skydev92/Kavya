import warnings
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional, Callable, AsyncGenerator, List, Tuple, Set, Dict

import pandas as pd
import litellm
from litellm import (
    acompletion, 
    completion, 
    batch_completion, 
    get_supported_openai_params,
    supports_response_schema,
    supports_function_calling
)
from textwrap import dedent
from tqdm import tqdm

import os
import json
import logging
import sys
import re
import inspect
import enum
from threading import Lock
import time
import asyncio
import random

from routellm.models import (
    ChatCompletionRequest, ContentRequest, ContentStrategy, 
    HTMLTagStrategy, OutlineSection, ContentOutline, 
    ContentDraft, FullContent, RoutingAnalysis
)
from routellm.routers.routers import ROUTER_CLS
from pydantic import BaseModel

# Model translation mapping
def get_model_translations(config):
    """Get model translations from config."""
    if not config or "model_translations" not in config:
        raise ValueError("Config must include model_translations")
    return config["model_translations"]

# Default config for routers augmented using golden label data from GPT-4.
# This is exactly the same as config.example.yaml.
GPT_4_AUGMENTED_CONFIG = {
    "sw_ranking": {
        "arena_battle_datasets": [
            "lmsys/lmsys-arena-human-preference-55k",
            "routellm/gpt4_judge_battles",
        ],
        "arena_embedding_datasets": [
            "routellm/arena_battles_embeddings",
            "routellm/gpt4_judge_battles_embeddings",
        ],
    },
    "causal_llm": {"checkpoint_path": "routellm/causal_llm_gpt4_augmented"},
    "bert": {"checkpoint_path": "routellm/bert_gpt4_augmented"},
    "mf": {"checkpoint_path": "routellm/mf_gpt4_augmented"},
}

DEFAULT_CHUNK_SIZE = 10
LONGWRITER_ONLY_ARGS = ["allowed_html_tags", "allowed_html_classes"]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

class RoutingError(Exception):
    pass

class AgentMemory:
    """
    Stores data that can be used to augment the context of a chat completion request.

    This can be used to store data that is relevant to the entire conversation, and
    can be used by the router to make routing decisions.

    Attributes:
        data (Dict[str, Any]): a dictionary of key-value pairs, where the key is a
            string and the value is any type of object.
    """

    def __init__(self):
        self.data: Dict[str, Any] = {}

    def set(self, key: str, value: Any):
        self.data[key] = value

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def clear(self):
        self.data.clear()


@dataclass
class ModelPair:
    strong: str
    weak: str


class RequestCostTracker:
    def __init__(self):
        self.cost = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.lock = Lock()
    
    def add_cost(self, cost: float):
        if cost is not None:  # Only add cost if it's not None
            with self.lock:
                self.cost += cost
    
    def update_prompt_tokens(self, tokens: int):
        with self.lock:
            self.prompt_tokens += tokens
    
    def update_completion_tokens(self, tokens: int):
        with self.lock:
            self.completion_tokens += tokens
    
    def get_total(self) -> float:
        with self.lock:
            return self.prompt_tokens + self.completion_tokens

class Controller:
    def __init__(
        self,
        routers: list[str],
        strong_model: str,
        weak_model: str,
        config: Optional[dict[str, dict[str, Any]]] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        progress_bar: bool = False,
        suppress_warnings: bool = False,
    ):
        # Require config with all necessary settings
        if not config:
            raise ValueError("Config is required")
            
        # Validate all required config sections
        if "model_translations" not in config:
            raise ValueError("Config must include model_translations")
        if "general_settings" not in config:
            raise ValueError("Config must include general_settings")
        if "basic_router_max_chars" not in config["general_settings"]:
            raise ValueError("general_settings must include basic_router_max_chars")

        # Initialize model pair
        self.model_pair = ModelPair(strong=strong_model, weak=weak_model)
        self.routers = {}
        self.api_base = api_base
        self.api_key = api_key
        self.model_counts = defaultdict(lambda: defaultdict(int))
        self.progress_bar = progress_bar
        self.suppress_warnings = suppress_warnings
        self.cost_tracker = RequestCostTracker()
        self.memory = AgentMemory()
        self.user = None  # Will be set during completion calls
        
        # Load model translations
        self.model_translations = config["model_translations"]
        self.basic_router_max_chars = config["general_settings"]["basic_router_max_chars"]
        
        # Load fallback configurations if available
        self.fallback_configs = config.get("fallback_configs", {})

        router_pbar = None
        if progress_bar:
            router_pbar = tqdm(routers)
            tqdm.pandas()

        for router in routers:
            if router_pbar is not None:
                router_pbar.set_description(f"Loading {router}")
            self.routers[router] = ROUTER_CLS[router](**config.get(router, {}))

        # Some Python magic to match the OpenAI Python SDK
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self.completion, acreate=self.acompletion
            )
        )
        
        # Load predefined prompts
        self.predefined_prompts = {}
        self.load_predefined_prompts()

    def load_predefined_prompts(self):
        """Load predefined prompts from a JSON file."""
        file_path = os.path.join(os.path.dirname(__file__), 'predefined_prompts.json')
        try:
            with open(file_path, 'r') as f:
                self.predefined_prompts = json.load(f)
                return self.predefined_prompts
        except:
            self.predefined_prompts = {}
            return {}

    def check_predefined_prompt(self, message):
        for key, value in self.predefined_prompts.items():
            if key in message:
                return value
        return None

    def _validate_router_threshold(
        self, router: Optional[str], threshold: Optional[float]
    ):
        if router not in self.routers:
            raise RoutingError(f"Router {router} not found.")
        if threshold is None or threshold < 0 or threshold > 1:
            raise RoutingError(f"Threshold {threshold} must be between 0 and 1.")

    def _parse_model_name(self, model: str):
        if model.startswith("router-"):
            parts = model.split("-")
            if len(parts) != 3:
                raise RoutingError(
                    f"Invalid model name {model}. Expected format: router-<router>-<threshold>"
                )
            router = parts[1]
            try:
                threshold = float(parts[2])
            except ValueError as e:
                raise RoutingError(f"Threshold {threshold} must be a float.") from e
            return router, threshold
        else:
            return None, None

    def _get_routed_model_for_completion(
        self, messages: list, router: str, threshold: float
    ):
        prompt = messages[-1]["content"]
        routed_model = self.routers[router].route(prompt, threshold, self.model_pair)

        # Commented because variable is never used and can cause bugs, the dict is not initialized properly.
        # self.model_counts[routed_model] += 1

        return routed_model

    def batch_calculate_win_rate(
        self,
        prompts: pd.Series,
        router: str,
        threshold: float,
    ):
        return self.routers[router].batch_calculate_win_rate(
            prompts, threshold, self.model_pair
        )

    def route(self, prompt: str, router: str, threshold: float):
        self._validate_router_threshold(router, threshold)

        return self.routers[router].route(prompt, threshold, self.model_pair)

    def completion(
        self,
        *,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        **kwargs,
    ):
        # Ensure user ID is present and valid
        if "user" not in kwargs or not kwargs["user"]:
            error_msg = "CRITICAL: No user ID provided in completion request. Every request must be associated with a user."
            logging.error(error_msg)
            raise ValueError(error_msg)
            
        try:
            user_id = int(kwargs["user"])  # Validate user ID is a valid integer
        except (ValueError, TypeError):
            error_msg = "CRITICAL: Invalid user ID format. User ID must be a valid integer."
            logging.error(error_msg)
            raise ValueError(error_msg)

        model = kwargs.get('model', 'unspecified')
        logging.info(f"Making {'streaming' if kwargs.get('stream') else 'non-streaming'} completion call using model: {model}")
        
        # Store original_model if provided, before any model selection logic
        if 'original_model' in kwargs:
            self.original_model = kwargs.pop('original_model')
            
        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                return {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": predefined_answer}
                        }
                    ],
                    "model": "predefined_prompt"
                }

        # Capture all arguments for get_model
        frame = inspect.currentframe()
        args, _, _, values = inspect.getargvalues(frame)
        get_model_args = {arg: values[arg] for arg in args if arg != "self"}
        get_model_args.update(kwargs)

        # Call get_model with all arguments
        model = self.get_model(**get_model_args)
        kwargs["model"] = model

        # Handle structured output configuration
        if "config" in kwargs and kwargs["config"]:
            config = kwargs["config"]
            if isinstance(config, dict):
                # Check if model supports response_format and json_schema
                supported_params = get_supported_openai_params(model=model)
                has_schema_support = supports_response_schema(model=model)
                
                if "response_schema" in config:
                    if not has_schema_support:
                        logging.warning(f"Model {model} does not support json_schema. Enabling client-side validation.")
                        # Enable client-side validation for models that don't support schema
                        kwargs["config"]["enable_json_schema_validation"] = True
                    
                    schema = config["response_schema"]
                    if isinstance(schema, type) and issubclass(schema, BaseModel):
                        # Convert Pydantic model to JSON schema
                        config["response_schema"] = {
                            "type": "json_schema",
                            "json_schema": schema.model_json_schema(),
                            "strict": True
                        }
                    elif isinstance(schema, type) and issubclass(schema, enum.Enum):
                        if "response_format" not in supported_params:
                            logging.warning(f"Model {model} does not support response_format. Enum responses may not work as expected.")
                        # Convert enum to proper format
                        config["response_schema"] = {
                            "type": "string",
                            "enum": [e.value for e in schema],
                        }
                        config["response_mime_type"] = "text/x.enum"
                    elif isinstance(schema, dict):
                        # Already in proper format, ensure it has type and strict fields
                        if "type" not in schema:
                            schema["type"] = "json_schema"
                        if "strict" not in schema:
                            schema["strict"] = True
                        config["response_schema"] = schema

        # Only necessary for the longwriter
        for key in LONGWRITER_ONLY_ARGS:
            if key in kwargs:
                del kwargs[key]

        if self.suppress_warnings:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                response = completion(api_base=self.api_base, api_key=self.api_key, **kwargs)
        else:
            response = completion(api_base=self.api_base, api_key=self.api_key, **kwargs)

        # Handle enum responses
        if (
            "config" in kwargs 
            and kwargs["config"] 
            and kwargs["config"].get("response_mime_type") == "text/x.enum"
            and "choices" in response
            and response["choices"]
            and "message" in response["choices"][0]
        ):
            enum_value = response["choices"][0]["message"]["content"].strip()
            enum_class = kwargs["config"]["response_schema"]
            if isinstance(enum_class, type) and issubclass(enum_class, enum.Enum):
                enum_response = EnumResponse.from_enum(enum_class, enum_value)
                response["choices"][0]["message"]["content"] = enum_response.model_dump()

        # If we have an original_model stored, use it in the response
        if hasattr(self, 'original_model'):
            if isinstance(response, dict):
                response['model'] = self.original_model
            else:
                response.model = self.original_model

        return response

    async def acompletion_with_fallbacks(
        self,
        messages,
        model,
        original_model=None,
        max_retries=2,
        cooldown_seconds=60,
        **kwargs,
    ):
        """Complete with fallbacks, for the acompletion method."""
        cooling_down_models = set()  # Track models we're cooling down from rate limits
        tried_models = []
        current_attempt = 0
        total_attempts = 5  # Increase total attempts for more resilience
        
        # Get the fallback chain for the model, if it exists
        model_to_use = original_model if original_model else model
        fallback_chain = self.get_fallback_chain(model_to_use)
        
        # By this point, fallback_chain should already be filtered to start after the failed model
        if not fallback_chain:
            logging.warning(f"No fallback models available for {model_to_use} or all models have been tried")
            raise Exception(f"No fallback models available for {model_to_use}")
            
        logging.info(f"Attempting fallbacks with chain: {fallback_chain}")
        
        # Add exponential backoff for retry attempts
        base_delay = 0.5  # Start with a small delay
        
        while current_attempt < total_attempts and fallback_chain:
            for fallback_model in fallback_chain:
                # Skip models that are cooling down from rate limits
                if fallback_model in cooling_down_models:
                    logging.info(f"Skipping cooled-down model: {fallback_model}")
                    continue
                
                tried_models.append(fallback_model)
                logging.info(f"Attempting completion with fallback model: {fallback_model}")
                
                for attempt in range(max_retries):
                    try:
                        # Apply jitter to avoid thundering herd problem
                        jitter = random.uniform(0.8, 1.2)
                        delay = base_delay * (2 ** current_attempt) * jitter
                        
                        if attempt > 0:
                            # Add increasing delay between retry attempts
                            logging.info(f"Retry attempt {attempt+1}/{max_retries} for {fallback_model} after {delay:.2f}s delay")
                            await asyncio.sleep(delay)
                        
                        # Create a new kwargs dict with the current fallback model
                        current_kwargs = kwargs.copy()
                        current_kwargs["model"] = fallback_model
                        current_kwargs["messages"] = messages
                        
                        # Use litellm's acompletion directly instead of going through provider
                        result = await acompletion(api_base=self.api_base, api_key=self.api_key, **current_kwargs)
                        
                        # Convert Usage objects to dictionaries to ensure JSON serialization works
                        if result and hasattr(result, 'usage') and not isinstance(result.usage, dict):
                            # For Pydantic models (preferred approach)
                            if hasattr(result.usage, 'model_dump'):
                                result.usage = result.usage.model_dump()
                            # Fallback for non-Pydantic objects
                            elif hasattr(result.usage, '__dict__'):
                                result.usage = vars(result.usage)
                        
                        logging.info(f"Successful completion with fallback model: {fallback_model}")
                        return result
                        
                    except litellm.RateLimitError as e:
                        logging.warning(f"Rate limit hit for fallback model {fallback_model}, cooling down for {cooldown_seconds}s: {e}")
                        cooling_down_models.add(fallback_model)
                        break  # Break and try next model in fallback chain
                        
                    except Exception as e:
                        logging.warning(f"Error with fallback model {fallback_model} (attempt {attempt+1}/{max_retries}): {e}")
                        
                        if attempt == max_retries - 1:
                            if attempt > 0:  # Only log error for final attempt if we've tried more than once
                                logging.error(f"All attempts failed for fallback model {fallback_model}: {e}")
            
            # If we've tried all models in the fallback chain, wait before trying again
            await asyncio.sleep(1.0)  # Add pause between cycles
            current_attempt += 1
            logging.info(f"Tried all fallback models and failed. Starting cycle {current_attempt+1}/{total_attempts}")
        
        # If we've exhausted all attempts with all models, raise an exception
        raise Exception(f"All fallback models failed. Tried: {', '.join(tried_models)}")

    async def acompletion(
        self,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        fallbacks: Optional[List[str]] = None,
        **kwargs,
    ):
        """Async completion method with support for fallbacks."""
        # Ensure user ID is present and valid
        if "user" not in kwargs or not kwargs["user"]:
            error_msg = "CRITICAL: No user ID provided in acompletion request. Every request must be associated with a user."
            logging.error(error_msg)
            raise ValueError(error_msg)
            
        try:
            user_id = int(kwargs["user"])  # Validate user ID is a valid integer
        except (ValueError, TypeError):
            error_msg = "CRITICAL: Invalid user ID format. User ID must be a valid integer."
            logging.error(error_msg)
            raise ValueError(error_msg)

        model = kwargs.get('model', 'unspecified')
        logging.info(f"DEBUG: acompletion called with model: {model}")
        logging.info(f"DEBUG: providers_list in kwargs: {kwargs.get('providers_list', 'None')}")
        logging.info(f"Making async {'streaming' if kwargs.get('stream') else 'non-streaming'} completion call using model: {model}")
        
        # Store original_model if provided, before any model selection logic
        if 'original_model' in kwargs:
            self.original_model = kwargs.pop('original_model')
            
        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                return {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": predefined_answer}
                        }
                    ],
                    "model": "predefined_prompt"
                }

        if "model" in kwargs:
            parsed_router, parsed_threshold = self._parse_model_name(kwargs["model"])
            router = router or parsed_router
            threshold = threshold or parsed_threshold
        
        if router and threshold:
            self._validate_router_threshold(router, threshold)
            kwargs["model"] = self._get_routed_model_for_completion(
                kwargs["messages"], router, threshold
            )
        elif "model" not in kwargs:
            raise RoutingError("No model specified and router/threshold not provided.")

        # Handle structured output configuration
        if "config" in kwargs and kwargs["config"]:
            config = kwargs["config"]
            if isinstance(config, dict):
                # Check if model supports response_format and json_schema
                supported_params = get_supported_openai_params(model=kwargs["model"])
                has_schema_support = supports_response_schema(model=kwargs["model"])
                
                if "response_schema" in config:
                    if not has_schema_support:
                        logging.warning(f"Model {kwargs['model']} does not support json_schema. Enabling client-side validation.")
                        # Enable client-side validation for models that don't support schema
                        kwargs["config"]["enable_json_schema_validation"] = True
                    
                    schema = config["response_schema"]
                    if isinstance(schema, type) and issubclass(schema, BaseModel):
                        # Convert Pydantic model to JSON schema
                        config["response_schema"] = {
                            "type": "json_schema",
                            "json_schema": schema.model_json_schema(),
                            "strict": True
                        }
                    elif isinstance(schema, type) and issubclass(schema, enum.Enum):
                        if "response_format" not in supported_params:
                            logging.warning(f"Model {kwargs['model']} does not support response_format. Enum responses may not work as expected.")
                        # Convert enum to proper format
                        config["response_schema"] = {
                            "type": "string",
                            "enum": [e.value for e in schema],
                        }
                        config["response_mime_type"] = "text/x.enum"
                    elif isinstance(schema, dict):
                        # Already in proper format, ensure it has type and strict fields
                        if "type" not in schema:
                            schema["type"] = "json_schema"
                        if "strict" not in schema:
                            schema["strict"] = True
                        config["response_schema"] = schema
        
        # Only necessary for the longwriter
        for key in LONGWRITER_ONLY_ARGS:
            if key in kwargs:
                del kwargs[key]
        
        # First try with the model selected by the router or provided directly
        try:
            # Keep the existing warning suppression logic
            if self.suppress_warnings:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=UserWarning)
                    response = await acompletion(api_base=self.api_base, api_key=self.api_key, **kwargs)
            else:
                response = await acompletion(api_base=self.api_base, api_key=self.api_key, **kwargs)
                
            # Handle enum responses
            if (
                "config" in kwargs 
                and kwargs["config"] 
                and kwargs["config"].get("response_mime_type") == "text/x.enum"
                and hasattr(response, "choices")
                and response.choices
                and hasattr(response.choices[0], "message")
            ):
                enum_value = response.choices[0].message.content.strip()
                enum_class = kwargs["config"]["response_schema"]
                if isinstance(enum_class, type) and issubclass(enum_class, enum.Enum):
                    enum_response = EnumResponse.from_enum(enum_class, enum_value)
                    response.choices[0].message.content = enum_response.model_dump()

            # If we have an original_model stored, use it in the response
            if hasattr(self, 'original_model'):
                if isinstance(response, dict):
                    response['model'] = self.original_model
                else:
                    response.model = self.original_model
                    
            return response
            
        except Exception as e:
            # If the primary model fails and we have fallbacks available, try them
            if fallbacks or (hasattr(self, 'original_model') and self.original_model in self.fallback_configs):
                logging.warning(f"Primary model {kwargs['model']} failed: {str(e)}. Activating fallback chain.")
                
                # If explicit fallbacks were provided, use those
                if fallbacks:
                    logging.info(f"Using explicitly provided fallback chain: {fallbacks}")
                    # Try the fallback chain, skipping the current model if it's in the chain
                    if kwargs["model"] in fallbacks:
                        idx = fallbacks.index(kwargs["model"]) + 1
                        if idx < len(fallbacks):
                            fallbacks = fallbacks[idx:]
                        else:
                            fallbacks = []
                # Check for providers_list in the request data (from providers parameter)
                elif "providers_list" in kwargs:
                    providers_list = kwargs.pop("providers_list")
                    logging.info(f"Using custom providers list: {providers_list}")
                    
                    # If the current model is the first in the providers list, use the rest as fallbacks
                    if providers_list and kwargs["model"] == providers_list[0]:
                        if len(providers_list) > 1:
                            fallbacks = providers_list[1:]  # Skip the first model (current one)
                            logging.info(f"Using remaining models from providers list as fallbacks: {fallbacks}")
                        else:
                            fallbacks = []
                            logging.warning(f"No fallbacks available in providers list after {kwargs['model']}")
                    # If the current model is elsewhere in the providers list, use the rest as fallbacks
                    elif kwargs["model"] in providers_list:
                        idx = providers_list.index(kwargs["model"]) + 1
                        if idx < len(providers_list):
                            fallbacks = providers_list[idx:]
                            logging.info(f"Starting fallback chain from next model after {kwargs['model']}")
                        else:
                            fallbacks = []
                            logging.warning(f"No more fallbacks available after {kwargs['model']}")
                    else:
                        # If the current model isn't in the providers list, use the entire list as fallbacks
                        fallbacks = providers_list
                        logging.info(f"Using entire providers list as fallbacks: {fallbacks}")
                # Otherwise check for fallbacks in the config
                elif hasattr(self, 'original_model') and self.original_model in self.fallback_configs:
                    # Get fallback models from config
                    fallback_config = self.fallback_configs[self.original_model]
                    fallbacks = fallback_config.get('models', [])
                    max_retries = fallback_config.get('max_retries', 3)
                    retry_delay = fallback_config.get('retry_delay', 1.0)
                    
                    # Skip the failed model if it's in the fallback chain
                    if kwargs["model"] in fallbacks:
                        idx = fallbacks.index(kwargs["model"]) + 1
                        if idx < len(fallbacks):
                            fallbacks = fallbacks[idx:]
                            logging.info(f"Starting fallback chain from next model after {kwargs['model']}")
                        else:
                            fallbacks = []
                            logging.warning(f"No more fallbacks available after {kwargs['model']}")
                
                # Use fallbacks if available
                if fallbacks:
                    logging.info(f"Using fallback chain: {fallbacks}")
                    try:
                        return await self.acompletion_with_fallbacks(
                            messages=kwargs["messages"],
                            model=kwargs["model"],
                            original_model=getattr(self, 'original_model', None),
                            max_retries=max_retries if 'max_retries' in locals() else 2,
                            cooldown_seconds=retry_delay if 'retry_delay' in locals() else 60,
                            **kwargs
                        )
                    except Exception as fallback_error:
                        logging.error(f"All fallbacks failed: {str(fallback_error)}")
                        # Re-raise the original error to maintain the original failure context
                        raise e
                else:
                    logging.warning("No fallback models left to try")
                    
            # If no fallbacks or all fallbacks failed, re-raise the exception
            logging.error(f"No fallbacks configured or all fallbacks exhausted for {kwargs['model']}: {str(e)}")
            raise

    def get_model(
        self,
        *,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        **kwargs
    ):
        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                return "predefined_prompt"

        if "model" in kwargs:
            parsed_router, parsed_threshold = self._parse_model_name(kwargs["model"])
            router = router or parsed_router
            threshold = threshold or parsed_threshold

        if router and threshold:
            self._validate_router_threshold(router, threshold)
            kwargs["model"] = self._get_routed_model_for_completion(
                kwargs["messages"], router, threshold
            )
        elif "model" not in kwargs:
            raise RoutingError("No model specified and router/threshold not provided.")

        return kwargs["model"]

    def get_fallback_chain(self, model_name):
        """
        Get the fallback chain for a specific model.
        
        Args:
            model_name: The name of the model to get fallbacks for
            
        Returns:
            List of model names to try in sequence, or None if no fallback is configured
        """
        if not model_name:
            logging.warning("Cannot get fallback chain for None model name")
            return None
            
        # Check if we have a specific fallback config for this model
        if model_name in self.fallback_configs:
            fallback_models = self.fallback_configs[model_name].get('models', [])
            
            # Validate the fallback chain
            if not fallback_models:
                logging.warning(f"Empty fallback chain configured for model: {model_name}")
            else:
                logging.debug(f"Found fallback chain for model {model_name}: {fallback_models}")
                
            return fallback_models
            
        # No fallback chain defined
        logging.debug(f"No fallback chain found for model: {model_name}")
        return None

class TokenAccumulator:
    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.chunk_size = max(1, chunk_size)
        self.buffer: List[str] = []
        self.token_count = 0
    
    async def add_token(self, token: str) -> Optional[Tuple[str, int]]:
        """Add a token to the buffer and return accumulated tokens if chunk size is reached."""
        self.buffer.append(token)
        self.token_count += 1  # Each token from the model is one token
        if self.token_count >= self.chunk_size:
            result = ''.join(self.buffer)
            tokens_to_return = self.token_count
            self.buffer = []
            self.token_count = 0
            return result, tokens_to_return
        return None, 0
    
    async def flush(self) -> Optional[Tuple[str, int]]:
        """Flush any remaining tokens in the buffer."""
        if self.buffer:
            result = ''.join(self.buffer)
            tokens_to_return = self.token_count
            self.buffer = []
            self.token_count = 0
            return result, tokens_to_return
        return None, 0

class Longwriter(Controller):
    def __init__(
        self,
        **kwargs
    ):
        # Constructor logic inherited from parent class
        super().__init__(**kwargs)
        # Initialize content writer messages
        self.content_writer_messages = None
        self.cost_tracker = RequestCostTracker()
        self.memory = AgentMemory()

    async def get_content_strategy(self, request: ContentRequest, model: str) -> ContentStrategy:
        logging.info(f"Making completion call for content strategy using model: {model}")
        # Reset cost tracker for new request
        self.cost_tracker = RequestCostTracker()
        self.user = request.user
        # First check if model supports response schema
        # Commented cos not reliable (e.g. mistral-medium)
        # if not supports_function_calling(model=model):
        #     raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

        content_strategist_prompt = '''
        You are a professional content strategist. Create a comprehensive content strategy that ensures:
        - Expertise in the subject matter
        - Authority in the field
        - Trustworthiness of information
        - User engagement and value
        
        Provide a content strategy that incorporates these principles without explicitly referencing them.
        '''
        
        # Store the original messages for later use
        original_messages = None
        if hasattr(request, 'messages'):
            original_messages = request.messages
        
        try:
            response = await acompletion(
                api_base=self.api_base,
                api_key=self.api_key,
                model=model,  # Direct model use after routing decision
                messages=[
                    {"role": "system", "content": str(dedent(content_strategist_prompt))},
                    {"role": "user", "content": str(request.prompt)}
                ],
                response_format=ContentStrategy,
                user=request.user  # Propagate user ID
            )
            
            # Update cost tracker with response usage
            if hasattr(response, 'usage'):
                self.cost_tracker.update_prompt_tokens(response.usage.prompt_tokens)
                self.cost_tracker.update_completion_tokens(response.usage.completion_tokens)
            
            # Get the content from the response
            content = response.choices[0].message.content
            logging.debug(f"\033[96mContent Strategy Response:\n{content}\033[0m")
            
            strategy = ContentStrategy.model_validate_json(content)
            # Add the original messages and user ID to the strategy object
            strategy.original_messages = original_messages
            strategy.user = request.user  # Set the user ID
            return strategy
            
        except Exception as e:
            logging.error(f"\033[91mError creating content strategy: {str(e)}\033[0m")
            raise

    async def get_html_strategy(self, allowed_html_tags: str, allowed_html_classes: str = "", content_strategy: ContentStrategy = None, model: str = None) -> HTMLTagStrategy:
        logging.info(f"Making completion call for HTML strategy using model: {model}")
        # First check if model supports response schema
        # Commented cos not reliable (e.g. mistral-medium)
        # if not supports_function_calling(model=model):
        #     raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

        html_strategist_prompt = f'''
        You are an HTML strategist. Given a list of allowed HTML tags, allowed HTML classes, and a content strategy, 
        provide a list of HTML tags and classes that would be most effective for structuring the content.
        '''
        
        try:
            response = await acompletion(
                model=model,  # Direct model use after routing decision
                api_base=self.api_base,
                api_key=self.api_key,
                messages=[
                    {"role": "system", "content": dedent(html_strategist_prompt)},
                    {"role": "user", "content": f"Allowed HTML tags: {allowed_html_tags}\nAllowed HTML classes: {allowed_html_classes}\nContent strategy: {content_strategy.model_dump_json()}"}
                ],
                response_format=HTMLTagStrategy,
                user=content_strategy.user  # Get user ID directly from content_strategy
            )

            # Update cost tracker with response usage
            if hasattr(response, 'usage'):
                self.cost_tracker.update_prompt_tokens(response.usage.prompt_tokens)
                self.cost_tracker.update_completion_tokens(response.usage.completion_tokens)

            # Get the content from the response
            content = response.choices[0].message.content
            logging.debug(f"\033[96mHTML Strategy Response:\n{content}\033[0m")
            
            return HTMLTagStrategy.model_validate_json(content)
            
        except Exception as e:
            logging.error(f"\033[91mError creating HTML strategy: {str(e)}\033[0m")
            raise

    async def get_content_outline(self, content_strategy: ContentStrategy, html_strategy: HTMLTagStrategy, model: str) -> ContentOutline:
        logging.info(f"Making completion call for content outline using model: {model}")
        # First check if model supports response schema
        # Commented cos not reliable (e.g. mistral-medium)
        # if not supports_function_calling(model=model):
        #     raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

        content_outliner_prompt = '''
        You are a creative content outliner. Given a content strategy and HTML tag strategy, 
        create a detailed, structured outline for the content.
        
        Requirements:
        1. The sum of all section word counts should be within 10% of the total recommended word count
        2. Be creative and avoid archaic structures unless appropriate
        3. Each section should have clear, actionable content ideas
        4. Include multimedia suggestions based on available HTML tags
        '''
        
        try:
            response = await acompletion(
                model=model,  # Direct model use after routing decision
                api_base=self.api_base,
                api_key=self.api_key,
                messages=[
                    {"role": "system", "content": dedent(content_outliner_prompt)},
                    {"role": "user", "content": f"Content strategy: {content_strategy.model_dump_json()}\nHTML strategy: {html_strategy.model_dump_json()}"}
                ],
                response_format=ContentOutline,
                user=content_strategy.user  # Get user ID from content strategy
            )

            # Update cost tracker with response usage
            if hasattr(response, 'usage'):
                self.cost_tracker.update_prompt_tokens(response.usage.prompt_tokens)
                self.cost_tracker.update_completion_tokens(response.usage.completion_tokens)

            # Get the content from the response
            content = response.choices[0].message.content
            logging.debug(f"\033[96mContent Outline Response:\n{content}\033[0m")
            
            return ContentOutline.model_validate_json(content)
            
        except Exception as e:
            logging.error(f"\033[91mError creating content outline: {str(e)}\033[0m")
            raise

    async def get_content_draft(
        self,
        section: OutlineSection, 
        content_strategy: ContentStrategy, 
        html_strategy: HTMLTagStrategy, 
        outline: ContentOutline, 
        model: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> AsyncGenerator:
        logging.info(f"Making streaming completion call for content draft section '{section.title}' using model: {model}")
        # Extract tone from system prompt if present
        tone_instruction = ""
        image_instructions = None
        
        # Get the original request messages from content_strategy
        if hasattr(content_strategy, 'original_messages') and content_strategy.original_messages:
            messages = content_strategy.original_messages
            if messages and messages[0]["role"] == "system":
                import re
                # Extract tone
                tone_match = re.search(r'<TONE>(.*?)</TONE>', messages[0]["content"], re.DOTALL)
                if tone_match:
                    tone = tone_match.group(1).strip()
                    tone_instruction = f"\n9. Use this specific tone of voice:\n{tone}"
                
                # Extract image handling instructions
                image_match = re.search(r'<IMAGE_HANDLING>(.*?)</IMAGE_HANDLING>', messages[0]["content"], re.DOTALL)
                if image_match:
                    image_instructions = image_match.group(1).strip()

        content_writer_prompt = f'''
        You are a creative content writer. Write the next section of content based on the given outline and strategy.
        This section is part of a larger article, so ensure continuity with previous sections.

        Key points:
        1. Use ONLY these HTML tags: {", ".join(html_strategy.tags)}
        2. Do NOT use <!DOCTYPE>, <html>, <head>, or <body> tags
        3. Start directly with content using allowed tags
        4. Follow the content strategy and address key questions
        5. Aim for {section.target_word_count} words
        6. Be creative and engaging
        7. Ensure continuity with previous sections, avoid repetitive phrases
        8. Keep in mind the overall structure of the article as outlined
        9. Do NOT use any markdown formatting (no *, _, #, -,``` etc.)
        10. Only use the specified HTML tags for formatting{tone_instruction}
        '''

        if 'img' in html_strategy.tags:
            if image_instructions:
                content_writer_prompt += f'''
        Image Requirements:
        {image_instructions}
        '''
            else:
                content_writer_prompt += '''
        Image Requirements:
        - Every <img> needs src and alt attributes
        - Place images between content blocks, not inline with text
        - Format src URLs as: https://promptahuman.com/600x400@2x?bg_color=[color]&&title=[file_name.png]&prompt=[Creative Brief]
        - Use these bg_colors: ghostwhite, whitesmoke, aliceblue, seashell, mintcream, ivory, azure, floralwhite
        - Alt text must be descriptive
        - Creative brief should be 10-20 words, emoji allowed
        - File name should match content type (jpg for images, gif for animations, mp4 for videos)

        Example:
        <img src="https://promptahuman.com/600x400@2x?bg_color=ghostwhite&&title=team_collaboration.jpg&prompt=Diverse team working together at modern office desk, sharing ideas 🤝✨" alt="Diverse team collaborating at a modern workspace, sharing creative ideas during a meeting">
        '''

        content_writer_prompt += f'''
        Here's the outline of the entire article:
        {outline.model_dump_json()}

        Now, write the next section: {section.title}
        '''

        # Initialize or update chat history
        if self.content_writer_messages is None:
            self.content_writer_messages = [
                {"role": "system", "content": content_writer_prompt},
                {"role": "user", "content": f"Write next section:\n{section.model_dump_json()}"}
            ]
        else:
            # For subsequent sections, use the same format
            self.content_writer_messages.append(
                {"role": "user", "content": f"Write next section:\n{section.model_dump_json()}"}
            )

        response = await acompletion(
            model=model,  # Direct model use after routing decision
            messages=self.content_writer_messages,  # Use the maintained message history
            stream=True,
            api_base=self.api_base,
            api_key=self.api_key,
            user=content_strategy.user  # Get user ID from content strategy
        )
        
        # Use provided chunk_size or default
        accumulator = TokenAccumulator(chunk_size=chunk_size)
        full_response = ""
        
        async for chunk in response:
            if chunk.choices[0].delta.content is not None:
                token = chunk.choices[0].delta.content
                full_response += token
                accumulated, token_count = await accumulator.add_token(token)
                if accumulated:
                    yield accumulated, token_count
        
        # Add the complete response to message history
        self.content_writer_messages.append({
            "role": "assistant",
            "content": full_response
        })
        
        # Flush any remaining tokens
        final_chunk, final_count = await accumulator.flush()
        if final_chunk:
            yield final_chunk, final_count

    async def content_creation_agent(self, request: ContentRequest, model: str):
        """Main content generation method that coordinates the content creation process."""
        # Ensure user ID is present in the request
        if not hasattr(request, 'user') or not request.user:
            error_msg = "CRITICAL: No user ID provided in content creation request. Every request must be associated with a user."
            logging.error(error_msg)
            raise ValueError(error_msg)
            
        try:
            user_id = int(request.user)  # Validate user ID is a valid integer
        except (ValueError, TypeError):
            error_msg = "CRITICAL: Invalid user ID format. User ID must be a valid integer."
            logging.error(error_msg)
            raise ValueError(error_msg)
            
        # Reset content writer messages for new content
        self.content_writer_messages = None
            
        content_strategy = await self.get_content_strategy(request, model)
        
        # Instead of yielding formatted strings, yield tuples with special message type
        # Create a JSON string for the disclosure
        disclosure_json = json.dumps(routellm.models.create_cost_disclosure_dict(
            prompt_tokens=self.cost_tracker.prompt_tokens,
            completion_tokens=self.cost_tracker.completion_tokens,
            description='Content strategy generation'
        ))
        # Yield as a tuple (content, token_count) to maintain consistent format
        yield disclosure_json, 0
        
        html_strategy = await self.get_html_strategy(request.allowed_html_tags, request.allowed_html_classes, content_strategy, model)
        
        # Disclose HTML strategy costs - using tuple format
        disclosure_json = json.dumps(routellm.models.create_cost_disclosure_dict(
            prompt_tokens=self.cost_tracker.prompt_tokens,
            completion_tokens=self.cost_tracker.completion_tokens,
            description='HTML strategy generation'
        ))
        yield disclosure_json, 0
        
        content_outline = await self.get_content_outline(content_strategy, html_strategy, model)
        
        # Disclose content outline costs - using tuple format
        disclosure_json = json.dumps(routellm.models.create_cost_disclosure_dict(
            prompt_tokens=self.cost_tracker.prompt_tokens,
            completion_tokens=self.cost_tracker.completion_tokens,
            description='Content outline generation'
        ))
        yield disclosure_json, 0
        
        for section in content_outline.sections:
            async for token, token_count in self.get_content_draft(
                section,
                content_strategy,
                html_strategy,
                content_outline,
                model
            ):
                yield token, token_count

    async def acompletion_stream(
        self,
        *,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        **kwargs,
    ):
        """Handle streaming responses for Longwriter"""
        if "model" in kwargs:
            parsed_router, parsed_threshold = self._parse_model_name(kwargs["model"])
            router = router or parsed_router
            threshold = threshold or parsed_threshold
        
        if router and threshold:
            self._validate_router_threshold(router, threshold)
            kwargs["model"] = self._get_routed_model_for_completion(
                kwargs["messages"], router, threshold
            )
        elif "model" not in kwargs:
            raise RoutingError("No model specified and router/threshold not provided.")
        
        # Check for predefined prompts in streaming context
        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                # For predefined prompts in async generators, we need to yield the content
                # instead of returning a dictionary
                logging.info("Using predefined prompt response in Longwriter")
                # Yield the entire predefined answer as a single token
                yield predefined_answer, len(predefined_answer.split())
                return  # Exit the generator after yielding the predefined answer
        
        request = ContentRequest(
            prompt=kwargs["messages"][-1]["content"],
            allowed_html_tags=kwargs.get("allowed_html_tags", ""),
            allowed_html_classes=kwargs.get("allowed_html_classes", ""),
            messages=kwargs.get("messages"),
            user=kwargs.get("user")
        )

        async for token, token_count in self.content_creation_agent(request, kwargs["model"]):
            yield token, token_count
            
    async def acompletion(
        self,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        fallbacks: Optional[List[str]] = None,
        **kwargs,
    ):
        """Override of base acompletion to handle longwriter-specific behavior."""
        # For streaming responses, delegate to acompletion_stream
        if kwargs.get("stream", False):
            return self.acompletion_stream(router=router, threshold=threshold, **kwargs)
        
        # For non-streaming, use the parent implementation
        return await super().acompletion(router=router, threshold=threshold, fallbacks=fallbacks, **kwargs)

class Controllers:
    def __init__(self, **kwargs):
        self.controllers = {}
        
        # Require config with general settings
        config = kwargs.get('config')
        if not config or 'general_settings' not in config:
            raise ValueError("Config must include general_settings with basic_router_max_chars")
            
        if 'basic_router_max_chars' not in config['general_settings']:
            raise ValueError("general_settings must include basic_router_max_chars")
            
        self.basic_router_max_chars = config['general_settings']['basic_router_max_chars']
        self.create_controller("default", **kwargs)

    def create_controller(self, id, **kwargs):
        # getting kwargs
        routers=kwargs.get('routers', None)
        config=kwargs.get('config', None)
        strong_model=kwargs.get('strong_model', None)
        weak_model=kwargs.get('weak_model', None)
        api_base=kwargs.get('api_base', None)
        api_key=kwargs.get('api_key', None)
        progress_bar=kwargs.get('progress_bar', None)

        if id.startswith("longwriter") :
            # initializing longwriter
            controller = Longwriter(
                routers=routers,
                config=config,
                strong_model=strong_model,
                weak_model=weak_model,
                api_base=api_base,
                api_key=api_key,
                progress_bar=progress_bar
            )
        else :
            # initializing controller
            controller = Controller(
                routers=routers,
                config=config,
                strong_model=strong_model,
                weak_model=weak_model,
                api_base=api_base,
                api_key=api_key,
                progress_bar=progress_bar
            )
        
        # storing controller
        self.controllers[id] = controller

        return controller
    
    """
    Call an async method on a controller with the given id.

    Parameters
    ----------
    request : ChatCompletionRequest
        The request to pass to the method.
    id : str
        The id of the controller to call.
    amethod_name : str
        The name of the async method to call.

    Returns
    -------
    The response from the async method.
    """
    async def response(self, request: ChatCompletionRequest, id, amethod_name, **kwargs):
        logging.info(f"DEBUG: response method called with id: {id}, method: {amethod_name}")
        logging.info(f"DEBUG: request model: {request.model}")
        logging.info(f"DEBUG: kwargs: {kwargs}")
        
        # Dump request to see all parameters
        request_dump = request.model_dump(exclude=LONGWRITER_ONLY_ARGS, exclude_none=True)
        logging.info(f"DEBUG: request dump: {request_dump}")
        
        return await self.controllers[id].__getattribute__(amethod_name)(
            **request.model_dump(exclude=LONGWRITER_ONLY_ARGS, exclude_none=True),
            **kwargs
        )

    async def basic_routing(self, request: ChatCompletionRequest, cost_tracker: Optional[RequestCostTracker] = None):
        logging.info("Making completion call for routing analysis using weak model")
        
        # Check if original_model is kavya-m1-hyper and skip routing if so
        request_data = request.model_dump()
        if request_data.get("original_model") == "kavya-m1-hyper":
            logging.info("Detected kavya-m1-hyper model, skipping routing and using simple completion")
            return "completion"
            
        # Ensure user ID is present in the request
        user_id = request_data.get("user")
        if not user_id:
            error_msg = "CRITICAL: No user ID provided in request. Every request must be associated with a user."
            logging.error(error_msg)
            raise ValueError(error_msg)
            
        # Get the default controller to use its methods
        default_controller = self.controllers["default"]
        
        # First determine which model to use based on router if specified
        routed_model = None
        if "model" in request_data:
            parsed_router, parsed_threshold = default_controller._parse_model_name(request_data["model"])
            if parsed_router and parsed_threshold:
                default_controller._validate_router_threshold(parsed_router, parsed_threshold)
                routed_model = default_controller._get_routed_model_for_completion(
                    request_data["messages"], parsed_router, parsed_threshold
                )
                # Store the routed model in the request for later use
                request.model = routed_model

        # Trim long prompts for basic router analysis
        prompt = request_data["messages"][-1]["content"]
        if len(prompt) > self.basic_router_max_chars:
            start = prompt[:self.basic_router_max_chars//3]  # Keep first third
            end = prompt[-self.basic_router_max_chars//3:]   # Keep last third
            prompt = f"{start}\n...[middle trimmed]...\n{end}"
        
        xml_guidance = ""
        if "<TASK>" in prompt:
            xml_guidance = """
IMPORTANT: For prompts containing <TASK> XML tags, analyze ONLY the task between the tags.
Example: If prompt contains "<TASK>write a one-line bio</TASK>" with 1000 words of context,
analyze only "write a one-line bio" - ignore both context length and complexity.
Focus on whether the requested task itself needs structure and organization,
not the structure of the provided context or reference materials.
"""

        routing_request = ChatCompletionRequest(
            model=default_controller.model_pair.weak,  # Always use weak model for checks
            messages=[
                {
                    "role": "system",
                    "content": f"""You are a routing analyzer that evaluates if content requires Longwriter's capabilities.
Analyze the prompt and return a JSON object that exactly matches this Pydantic model:

{RoutingAnalysis.model_json_schema()}
{xml_guidance}

When analyzing the prompt, set these fields accurately:

1. length_score: Estimate the probability (0.0-1.0) that the response will exceed 700 words based on the prompt's requirements.

2. needs_structure: Set to true if the content would benefit from organization into sections with headings.

3. is_data_dump: Set to true if the content is primarily lists or data without narrative flow.

For the needs_structure field specifically:
- Set needs_structure=FALSE for content that:
  * Requires a single, direct response
  * Can be answered in a few paragraphs
  * Doesn't benefit from organization into sections
  * Focuses on answering a specific question
  * Provides factual information without narrative
  * Requires minimal organization or flow
  * Can be presented in a linear, sequential manner
  * Doesn't need headings or subheadings

- Set needs_structure=TRUE for content that:
  * Benefits from being organized into multiple sections
  * Requires a logical flow with introduction, body, and conclusion
  * Would be enhanced by headings and subheadings
  * Needs a coherent narrative structure
  * Involves developing multiple related points or arguments
  * Requires strategic organization of information
  * Benefits from a planned outline
  * Needs to guide the reader through complex information
  * Would be improved by hierarchical organization"""
                },
                {
                    "role": "user",
                    "content": "Analyze this prompt: " + prompt
                }
            ],
            stream=False,  # Force non-streaming for routing
            response_format=RoutingAnalysis,
            user=request_data.get("user")  # Pass through the user ID
        )

        # Use regular completion for routing decision
        response = await acompletion(
            model=default_controller.model_pair.weak,  # Use weak model for routing decision
            messages=routing_request.messages,
            api_base=default_controller.api_base,
            api_key=default_controller.api_key,
            response_format=RoutingAnalysis,
            user=routing_request.user  # Pass through the user ID
        )
        
        # Add logging for routing analysis response and usage
        prompt_preview = format_prompt_preview(routing_request.messages[-1]["content"])
        usage_info = (
            f"Usage - Prompt Tokens: {response.usage.prompt_tokens}, "
            f"Completion Tokens: {response.usage.completion_tokens}, "
            f"Total Tokens: {response.usage.total_tokens}"
        )
        cost = response._hidden_params.get("response_cost", 0)
        
        if cost_tracker:
            cost_tracker.add_cost(cost)
        logging.info(format_usage_log(
            prompt_preview=prompt_preview, 
            usage_info=usage_info, 
            cost=cost, 
            user_id=int(routing_request.user) if routing_request.user else None,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens
        ))

        # Store router usage in the request object
        request.router_usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens
        }

        try:
            # Get the content directly from the response
            content = response.choices[0].message.content
            
            # Check if content is None or empty before trying to parse
            if not content:
                logging.error("Error: Empty or None content received from routing analysis")
                return "completion"
                
            analysis = RoutingAnalysis.model_validate_json(content)
            
            # Add debug output for routing decision
            logging.debug("\033[95m=== Routing Analysis ===\033[0m")
            logging.debug(f"\033[94mLength Score: {analysis.length_score}\033[0m")
            logging.debug(f"\033[94mNeeds Structure: {analysis.needs_structure}\033[0m")
            logging.debug(f"\033[94mIs Data Dump: {analysis.is_data_dump}\033[0m")
            
            # Route to longwriter if:
            # 1. Content will be long (> 700 words)
            # 2. Content benefits from structure
            # 3. Not just a data dump
            use_longwriter = (
                analysis.length_score > 0.7 and 
                analysis.needs_structure and 
                not analysis.is_data_dump
            )
            
            
            logging.debug(f"\033[93mDecision: {'Using Longwriter' if use_longwriter else 'Using Standard Completion'}\033[0m")
            logging.debug("\033[95m=====================\033[0m")
            
            return "longwriter" if use_longwriter else "completion"
        except Exception as e:
            logging.error(f"\033[91mError parsing routing analysis: {str(e)}\033[0m")
            # If there's any error parsing the response, default to completion
            return "completion"

    # METHODS TO IMITATE DICT INTERFACE

    def __repr__(self):
        return repr(self.controllers)

    def __del__(self):
        for controller in self.controllers.values():
            del controller

    def __getattr__(self, name):
        return self.controllers[name]

    def __contains__(self, id):
        return id in self.controllers

    def __getitem__(self, id):
        return self.controllers[id]

    def __setitem__(self, id, controller):
        self.controllers[id] = controller

    def __delitem__(self, id):
        del self.controllers[id]

    def __iter__(self):
        return iter(self.controllers)

    def __len__(self):
        return len(self.controllers)

    def __repr__(self):
        return repr(self.controllers)

    def __str__(self):
        return str(self.controllers)

def update_token_usage(user_id: Optional[int], prompt_tokens: Optional[int], completion_tokens: Optional[int]) -> None:
    """Update token usage in the database. Raises RuntimeError if update fails."""
    if user_id is not None and prompt_tokens is not None and completion_tokens is not None:
        try:
            from routellm.openai_server import app
            if not hasattr(app, 'db') or not app.db:
                error_msg = "CRITICAL: Database connection not available. Cannot proceed without updating token usage."
                logging.error(error_msg)
                raise RuntimeError(error_msg)
                
            app.db.update_usage_with_response(
                account_id=user_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens
            )
        except Exception as db_error:
            error_msg = f"CRITICAL: Failed to update token usage in database: {str(db_error)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from db_error

def format_usage_log(prompt_preview: str, usage_info: str, cost: Optional[float] = None, user_id: Optional[int] = None, prompt_tokens: Optional[int] = None, completion_tokens: Optional[int] = None) -> str:
    """Format usage log with consistent styling and simple borders."""
    # Update token usage before logging
    update_token_usage(user_id, prompt_tokens, completion_tokens)

    usage_msg = (
        f"\n================================================================\n"
        f"Account: {user_id if user_id is not None else 'No Account ID'}\n"
        f"Prompt: '{prompt_preview}'\n"
        f"{usage_info}"
    )
    if cost is not None:
        usage_msg += f"\nCost: ${cost:.6f}"
    usage_msg += "\n================================================================"
    return f"\033[94m{usage_msg}\033[0m"  # Using \033[94m for purple instead of \033[95m for pink

def format_prompt_preview(prompt: str, max_length: int = 50) -> str:
    """Format prompt preview by replacing newlines with spaces and truncating."""
    # Replace all whitespace (including newlines) with a single space
    cleaned = ' '.join(prompt.split())
    return cleaned[:max_length] + ('...' if len(cleaned) > max_length else '')

def format_total_cost_log(total_cost: float) -> str:
    return f"\033[95m=== Total Request Cost: ${total_cost:.6f} ===\033[0m"

def custom_cost_usage_callback(
    kwargs,                  # kwargs to completion
    completion_response,     # response from completion
    start_time, end_time    # start/end time
):
    # Get user ID from kwargs - hard fail if not present
    user = kwargs.get("user", None)
    if not user:
        error_msg = "CRITICAL: No user ID provided in callback. Every request must be billed to a user."
        logging.critical(error_msg)  # Changed from error to critical for proper severity
        raise ValueError(error_msg)
        
    messages = kwargs.get("messages", [])
    prompt_preview = format_prompt_preview(messages[-1]["content"]) if messages else "No prompt"
    is_streaming = kwargs.get("stream", False)
    
    if is_streaming:
        if "complete_streaming_response" in kwargs:
            usage = kwargs["complete_streaming_response"].usage
            cost = kwargs.get("response_cost", 0)
            usage_info = (
                f"Usage - Prompt Tokens: {usage.prompt_tokens}, "
                f"Completion Tokens: {usage.completion_tokens}, "
                f"Total Tokens: {usage.total_tokens}"
            )
            logging.info("=== STREAMING TOKEN UPDATE ===")
            logging.info(f"User ID: {user}")
            logging.info(f"Model: {kwargs.get('model', 'unknown')}")
            logging.info(f"Stream: {is_streaming}")
            logging.info(f"Prompt Preview: {prompt_preview}")
            logging.info(usage_info)
            logging.info(f"Cost: ${cost}")
            logging.info("=== END TOKEN UPDATE ===")
            
            logging.info(format_usage_log(  # Using info level for usage logs
                prompt_preview=prompt_preview, 
                usage_info=usage_info, 
                cost=cost, 
                user_id=int(user),
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens
            ))
    else:
        usage = completion_response.usage
        cost = kwargs.get("response_cost", 0)
        usage_info = (
            f"Usage - Prompt Tokens: {usage.prompt_tokens}, "
            f"Completion Tokens: {usage.completion_tokens}, "
            f"Total Tokens: {usage.total_tokens}"
        )
        logging.info("=== NON-STREAMING TOKEN UPDATE ===")
        logging.info(f"User ID: {user}")
        logging.info(f"Model: {kwargs.get('model', 'unknown')}")
        logging.info(f"Stream: {is_streaming}")
        logging.info(f"Prompt Preview: {prompt_preview}")
        logging.info(usage_info)
        logging.info(f"Cost: ${cost}")
        logging.info("=== END TOKEN UPDATE ===")
        
        logging.info(format_usage_log(  # Using info level for usage logs
            prompt_preview=prompt_preview, 
            usage_info=usage_info, 
            cost=cost, 
            user_id=int(user),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens
        ))

# Register the callback
litellm.success_callback = [custom_cost_usage_callback]
logging.info("Registered custom_cost_usage_callback with litellm")

# Example usage for non-streaming completion
def make_non_streaming_completion(prompt):
    response = litellm.completion(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    return response

# Example usage for streaming completion
def make_streaming_completion(prompt):
    response = litellm.completion(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        stream_options={"include_usage": True}  # Important for getting usage info in streaming
    )
    
    # Process streaming response
    collected_content = []
    for chunk in response:
        if chunk.choices[0].delta.content:
            collected_content.append(chunk.choices[0].delta.content)
    
    return "".join(collected_content)