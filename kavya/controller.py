import asyncio
import enum
import inspect
import json
import logging
import os
import re
import sys
import time
import warnings
from textwrap import dedent
from threading import Lock
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import litellm
from litellm import (
    acompletion,
    completion,
    get_supported_openai_params,
    supports_response_schema,
)
from pydantic import BaseModel

from kavya.constants import AgentType, ObservationName
from kavya.langfuse_helpers import (
    create_controller_metadata,
    create_langfuse_metadata,
    observation_manager,
)
from kavya.models import (
    ChatCompletionRequest,
    ContentOutline,
    ContentRequest,
    ContentStrategy,
    HTMLTagStrategy,
    OutlineSection,
    RoutingAnalysis,
)
from kavya.provider_chain import ProviderChain
from kavya.web_search import enhance_with_web_search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Reduce noise from external libraries
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)


class RoutingError(Exception):
    pass


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
        model: str,
        config: Optional[dict[str, dict[str, Any]]] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        progress_bar: bool = False,
        suppress_warnings: bool = False,
    ):
        # Require config with all necessary settings
        if not config:
            raise ValueError("Config is required")
        self.config = config  # Store the config dictionary

        # Validate all required config sections
        if "model_translations" not in self.config:
            raise ValueError("Config must include model_translations")
        if "general_settings" not in self.config:
            raise ValueError("Config must include general_settings")
        if "basic_router_max_chars" not in self.config["general_settings"]:
            raise ValueError("general_settings must include basic_router_max_chars")

        # Initialize model
        self.model = model
        logging.info(f"DEBUG: Controller creation using model: {self.model}")

        self.api_base = api_base
        self.api_key = api_key
        self.progress_bar = progress_bar
        self.suppress_warnings = suppress_warnings
        self.cost_tracker = RequestCostTracker()
        self.user = None  # Will be set during completion calls

        # Load model translations
        self.model_translations = self.config["model_translations"]
        self.basic_router_max_chars = self.config["general_settings"][
            "basic_router_max_chars"
        ]

        # Load fallback configurations if available
        self.fallback_configs = self.config.get("fallback_configs", {})

        # Load controller configuration
        controller_config = self.config["controller"]  # hard fail if missing
        self.default_chunk_size = controller_config["default_chunk_size"]
        self.longwriter_word_threshold = controller_config[
            "longwriter_word_threshold"
        ]  # hard fail if missing
        self.longwriter_only_args = controller_config["longwriter_only_args"]

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
        file_path = os.path.join(os.path.dirname(__file__), "predefined_prompts.json")
        try:
            with open(file_path, "r") as f:
                self.predefined_prompts = json.load(f)
                return self.predefined_prompts
        except (FileNotFoundError, json.JSONDecodeError):
            self.predefined_prompts = {}
            return {}

    def check_predefined_prompt(self, message):
        """Check if a message matches a predefined prompt and return the answer if it does."""
        for key, value in self.predefined_prompts.items():
            if key in message:
                return value

        return None

    def completion(
        self,
        **kwargs,
    ):
        # Ensure user ID is present and valid
        if "user" not in kwargs or not kwargs["user"]:
            error_msg = "CRITICAL: No user ID provided in completion request. Every request must be associated with a user."
            logging.error(error_msg, exc_info=True)
            raise ValueError(error_msg)

        try:
            user_id = int(kwargs["user"])  # Validate user ID is a valid integer
        except (ValueError, TypeError):
            error_msg = (
                "CRITICAL: Invalid user ID format. User ID must be a valid integer."
            )
            logging.error(error_msg, exc_info=True)
            raise ValueError(error_msg)

        model = kwargs.get("model", "unspecified")
        logging.info(
            f"Making {'streaming' if kwargs.get('stream') else 'non-streaming'} completion call using model: {model}"
        )

        # Store original_model if provided, before any model selection logic
        if "original_model" in kwargs:
            self.original_model = kwargs.pop("original_model")

        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                return {
                    "choices": [
                        {"message": {"role": "assistant", "content": predefined_answer}}
                    ],
                    "model": "predefined_prompt",
                }

        # Capture all arguments for get_model
        frame = inspect.currentframe()
        args, _, _, values = inspect.getargvalues(frame)
        get_model_args = {arg: values[arg] for arg in args if arg != "self"}
        get_model_args.update(kwargs)

        # Call get_model with all arguments
        logging.info("DEBUG : Getting model")
        model = self.get_model(**get_model_args)
        kwargs["model"] = model

        # Handle structured output configuration
        if "config" in kwargs and kwargs["config"]:
            config = kwargs["config"]
            if isinstance(config, dict):
                # Check if model supports response_format and json_schema
                logging.info(
                    f"DEBUG : current model for get_supported_openai_params and supports_response_schema is: {model}"
                )
                supported_params = get_supported_openai_params(model=model)
                has_schema_support = supports_response_schema(model=model)

                if "response_schema" in config:
                    if not has_schema_support:
                        logging.warning(
                            f"Model {model} does not support json_schema. Enabling client-side validation."
                        )
                        # Enable client-side validation for models that don't support schema
                        kwargs["config"]["enable_json_schema_validation"] = True

                    schema = config["response_schema"]
                    if isinstance(schema, type) and issubclass(schema, BaseModel):
                        # Convert Pydantic model to JSON schema
                        config["response_schema"] = {
                            "type": "json_schema",
                            "json_schema": schema.model_json_schema(),
                            "strict": True,
                        }
                    elif isinstance(schema, type) and issubclass(schema, enum.Enum):
                        if "response_format" not in supported_params:
                            logging.warning(
                                f"Model {model} does not support response_format. Enum responses may not work as expected."
                            )
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
        for key in self.longwriter_only_args:
            if key in kwargs:
                del kwargs[key]

        # Extract existing metadata if present
        existing_metadata = kwargs.pop("metadata", {})

        # Create standardized metadata using helper
        metadata = create_controller_metadata(
            user_id=kwargs.get("user"),
            controller_class=self.__class__.__name__,
            model_requested=(
                self.original_model
                if hasattr(self, "original_model")
                else kwargs.get("model")
            ),
            model_actual=kwargs.get("model"),
            is_sync=True,
            additional_metadata=existing_metadata,
        )

        if self.suppress_warnings:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                response = completion(
                    api_base=self.api_base,
                    api_key=self.api_key,
                    metadata=metadata,
                    **kwargs,
                )
        else:
            response = completion(
                api_base=self.api_base,
                api_key=self.api_key,
                metadata=metadata,
                **kwargs,
            )

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
                from kavya.models import EnumResponse

                enum_response = EnumResponse.from_enum(enum_class, enum_value)
                response["choices"][0]["message"][
                    "content"
                ] = enum_response.model_dump()

                # If we have an original_model stored, set the response correctly
                if hasattr(self, "original_model"):
                    if isinstance(response, dict):
                        response["model"] = model  # Actual model used (e.g., gpt-4o)
                        response["original_model"] = (
                            self.original_model
                        )  # Virtual model requested (e.g., kavya-m1)
                    else:
                        response.model = model  # Actual model used (e.g., gpt-4o)
                        response.original_model = (
                            self.original_model
                        )  # Virtual model requested (e.g., kavya-m1)

        return response

    async def acompletion(
        self,
        use_research_agent: bool = False,
        **kwargs,
    ):
        """Async completion method with unified ProviderChain-based fallbacks."""
        # Ensure user ID is present and valid
        if "user" not in kwargs or not kwargs["user"]:
            error_msg = "CRITICAL: No user ID provided in acompletion request. Every request must be associated with a user."
            logging.error(error_msg, exc_info=True)
            raise ValueError(error_msg)

        try:
            user_id = int(kwargs["user"])  # Validate user ID is a valid integer
        except (ValueError, TypeError):
            error_msg = (
                "CRITICAL: Invalid user ID format. User ID must be a valid integer."
            )
            logging.error(error_msg, exc_info=True)
            raise ValueError(error_msg)

        # Store original_model if provided, before any model selection logic
        if "original_model" in kwargs:
            self.original_model = kwargs.pop("original_model")

        # Get the ProviderChain from the request, which should have been set by openai_server.py
        provider_chain = kwargs.pop("provider_chain", None)

        if not provider_chain:
            # Fallback: create a simple chain with the current model
            current_model = kwargs.get("model", self.model)
            provider_chain = ProviderChain(providers=[current_model])
            logging.warning(
                f"FALLBACK: No ProviderChain found in request, created simple chain: {provider_chain}"
            )
        else:
            logging.debug(
                f"FALLBACK: Received ProviderChain from request: {provider_chain}"
            )

        # Log chain status
        logging.info(f"FALLBACK: Starting with chain: {provider_chain}")
        logging.debug(
            f"FALLBACK: Initial ProviderChain details - providers: {provider_chain.providers}, failed: {provider_chain.failed}, current_index: {provider_chain.current_index}"
        )

        search_results = []  # Initialize empty search results

        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                return {
                    "choices": [
                        {"message": {"role": "assistant", "content": predefined_answer}}
                    ],
                    "model": "predefined_prompt",
                }

            # Only perform web search if research agent is requested
            if use_research_agent:
                logging.info("WEB_SEARCH: Checking if web search enhancement is needed")
                logging.info("WEB_SEARCH_STATUS: Searching the web")
                kwargs["messages"], search_results = await enhance_with_web_search(
                    self, kwargs["messages"]
                )

        # Handle structured output configuration
        if "config" in kwargs and kwargs["config"]:
            config = kwargs["config"]
            if isinstance(config, dict):
                # Check if model supports response_format and json_schema
                logging.info(
                    f"DEBUG : current model for get_supported_openai_params and supports_response_schema is: {kwargs['model']}"
                )
                supported_params = get_supported_openai_params(model=kwargs["model"])
                has_schema_support = supports_response_schema(model=kwargs["model"])

                if "response_schema" in config:
                    if not has_schema_support:
                        logging.warning(
                            f"Model {kwargs['model']} does not support json_schema. Enabling client-side validation."
                        )
                        # Enable client-side validation for models that don't support schema
                        kwargs["config"]["enable_json_schema_validation"] = True

                    schema = config["response_schema"]
                    if isinstance(schema, type) and issubclass(schema, BaseModel):
                        # Convert Pydantic model to JSON schema
                        config["response_schema"] = {
                            "type": "json_schema",
                            "json_schema": schema.model_json_schema(),
                            "strict": True,
                        }
                    elif isinstance(schema, type) and issubclass(schema, enum.Enum):
                        if "response_format" not in supported_params:
                            logging.warning(
                                f"Model {kwargs['model']} does not support response_format. Enum responses may not work as expected."
                            )
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
        for key in self.longwriter_only_args:
            if key in kwargs:
                del kwargs[key]

        # Remove longwriter_word_threshold from kwargs before API call
        kwargs.pop("longwriter_word_threshold", None)

        # Main fallback loop using ProviderChain
        max_retries = self.config["general_settings"].get("provider_max_retries", 3)
        loop_count = 0
        while (current_model := provider_chain.next()) is not None:
            loop_count += 1
            kwargs["model"] = current_model
            logging.info(
                f"FALLBACK: Loop #{loop_count} - Attempting completion with model: {current_model}"
            )
            logging.debug(
                f"FALLBACK: ProviderChain state before attempt: {provider_chain}"
            )

            for retry in range(max_retries):
                try:
                    if retry > 0:
                        delay = 0.5 * (2**retry)  # Exponential backoff
                        logging.info(
                            f"FALLBACK: Retry {retry + 1}/{max_retries} for {current_model} after {delay}s delay"
                        )
                        await asyncio.sleep(delay)

                    # Make the completion request
                    # Extract existing metadata if present
                    existing_metadata = kwargs.pop("metadata", {})

                    # Create standardized metadata using helper
                    metadata = create_controller_metadata(
                        user_id=kwargs.get("user"),
                        controller_class=self.__class__.__name__,
                        model_requested=(
                            self.original_model
                            if hasattr(self, "original_model")
                            else kwargs.get("model")
                        ),
                        model_actual=current_model,
                        is_retry=(retry > 0),
                        is_fallback=(loop_count > 1),
                        retry_attempt=retry + 1,
                        additional_metadata={
                            **existing_metadata,
                            "provider_chain": (
                                str(provider_chain) if provider_chain else None
                            ),
                            "loop_count": loop_count,
                        },
                    )

                    if self.suppress_warnings:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", category=UserWarning)
                            response = await acompletion(
                                api_base=self.api_base,
                                api_key=self.api_key,
                                metadata=metadata,
                                **kwargs,
                            )
                    else:
                        response = await acompletion(
                            api_base=self.api_base,
                            api_key=self.api_key,
                            metadata=metadata,
                            **kwargs,
                        )

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
                        if isinstance(enum_class, type) and issubclass(
                            enum_class, enum.Enum
                        ):
                            from kavya.models import EnumResponse

                            enum_response = EnumResponse.from_enum(
                                enum_class, enum_value
                            )
                            response.choices[0].message.content = (
                                enum_response.model_dump()
                            )

                    # If we have an original_model stored, set the response correctly
                    if hasattr(self, "original_model"):
                        if isinstance(response, dict):
                            response["model"] = (
                                current_model  # Actual model used (e.g., gpt-4o)
                            )
                            response["original_model"] = (
                                self.original_model
                            )  # Virtual model requested (e.g., kavya-m1)
                        else:
                            response.model = (
                                current_model  # Actual model used (e.g., gpt-4o)
                            )
                            response.original_model = (
                                self.original_model
                            )  # Virtual model requested (e.g., kavya-m1)

                    # Store the successful model for streaming access
                    self.last_successful_model = current_model

                    logging.info(f"FALLBACK: Success with model: {current_model}")
                    logging.debug(
                        f"FALLBACK: Final ProviderChain state after success: {provider_chain}"
                    )
                    if use_research_agent:
                        logging.info("WEB_SEARCH: Adding search results to response")
                        return response, search_results
                    else:
                        return response

                except litellm.RateLimitError as e:
                    logging.warning(
                        f"FALLBACK: Rate limit hit for {current_model}: {e}"
                    )
                    provider_chain.mark_failed(current_model)
                    break  # Skip remaining retries for this model
                except Exception as e:
                    logging.warning(
                        f"FALLBACK: Error with {current_model} (attempt {retry + 1}/{max_retries}): {e}"
                    )
                    if retry == max_retries - 1:
                        # All retries failed for this model
                        provider_chain.mark_failed(current_model)
                        logging.error(
                            f"FALLBACK: All retries failed for {current_model}"
                        )
                        logging.debug(
                            f"FALLBACK: ProviderChain state after marking failed: {provider_chain}"
                        )
                        break

        # If we get here, all providers have failed
        failed_models = provider_chain.failed
        logging.error(f"FALLBACK: All providers failed. Tried: {failed_models}")
        logging.debug(
            f"FALLBACK: Final ProviderChain state when exiting loop: {provider_chain}"
        )
        logging.debug(f"FALLBACK: Total loop iterations: {loop_count}")
        raise Exception(f"All fallback providers failed. Tried: {failed_models}")

    def get_model(self, **kwargs):
        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                return "predefined_prompt"

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
            fallback_models = self.fallback_configs[model_name].get("models", [])

            # Validate the fallback chain
            if not fallback_models:
                logging.warning(
                    f"Empty fallback chain configured for model: {model_name}"
                )
            else:
                logging.debug(
                    f"Found fallback chain for model {model_name}: {fallback_models}"
                )

            return fallback_models

        # No fallback chain defined
        logging.debug(f"No fallback chain found for model: {model_name}")
        return None


class TokenAccumulator:
    def __init__(self, chunk_size: int):
        self.chunk_size = max(1, chunk_size)
        self.buffer: List[str] = []
        self.token_count = 0

    async def add_token(self, token: str) -> Optional[Tuple[str, int]]:
        """Add a token to the buffer and return accumulated tokens if chunk size is reached."""
        self.buffer.append(token)
        self.token_count += 1  # Each token from the model is one token
        if self.token_count >= self.chunk_size:
            result = "".join(self.buffer)
            tokens_to_return = self.token_count
            self.buffer = []
            self.token_count = 0
            return result, tokens_to_return
        return None, 0

    async def flush(self) -> Optional[Tuple[str, int]]:
        """Flush any remaining tokens in the buffer."""
        if self.buffer:
            result = "".join(self.buffer)
            tokens_to_return = self.token_count
            self.buffer = []
            self.token_count = 0
            return result, tokens_to_return
        return None, 0


class ConversationContext:
    """Isolated state container for a single longwriter conversation chain."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.content_writer_messages: Optional[List[Dict[str, str]]] = None
        self.cost_tracker = RequestCostTracker()
        self.created_at = time.time()

    def __repr__(self):
        return (
            f"ConversationContext(user_id={self.user_id}, created_at={self.created_at})"
        )


class Longwriter(Controller):
    def __init__(self, **kwargs):
        # Constructor logic inherited from parent class
        super().__init__(**kwargs)
        # No instance state - all state is now conversation-scoped

    def _filter_messages_to_user_only(self, request_messages):
        """
        Filter messages to only include user messages when there's a system+user pair.
        Returns only user messages for 2-message system+user pairs, otherwise returns all messages.
        """
        if (
            request_messages
            and len(request_messages) == 2
            and request_messages[0].get("role") == "system"
            and request_messages[1].get("role") == "user"
        ):
            # Return only the user message to avoid system formatting noise
            return [request_messages[1]]
        else:
            # Preserve all messages for other conversation formats
            return request_messages if request_messages else []

    async def get_content_strategy(
        self,
        request: ContentRequest,
        model: str,
        provider_chain: ProviderChain = None,
        conversation_ctx: ConversationContext = None,
    ) -> ContentStrategy:
        # Create a parent observation for the entire longwriter workflow
        observation_manager.create_observation_id(ObservationName.LONGWRITER)

        logging.info(
            f"Making completion call for content strategy using model: {model}"
        )

        # Use original provider chain to preserve failed state from routing
        if provider_chain:
            logging.debug(f"CONTENT_STRATEGY: Received ProviderChain: {provider_chain}")
            logging.debug(
                "CONTENT_STRATEGY: Using original chain to preserve failed state from routing"
            )
            # Reset current_index to allow trying all non-failed providers for this method
            provider_chain.current_index = 0
            logging.debug(
                f"CONTENT_STRATEGY: Reset current_index to 0 for fresh method attempt"
            )
        # Use conversation context instead of instance state
        if not conversation_ctx:
            conversation_ctx = ConversationContext(user_id=str(request.user))
        # First check if model supports response schema
        # Commented cos not reliable (e.g. mistral-medium)
        # if not supports_function_calling(model=model):
        #     raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

        content_strategist_prompt = """
        You are a professional content strategist. Create a comprehensive content strategy that ensures:
        - Expertise in the subject matter
        - Authority in the field
        - Trustworthiness of information
        - User engagement and value
        
        Provide a content strategy that incorporates these principles without explicitly referencing them. Focus on the user and their needs, find the user task and requirements in the prompt while ignoring irrelevant information like system prompts and other technical instructions.
        """

        # Prepare messages for the LLM call
        # Prepend the strategist system prompt to the messages from the request
        strategist_system_prompt = {
            "role": "system",
            "content": str(dedent(content_strategist_prompt)),
        }

        # Filter messages to focus on user content
        filtered_messages = self._filter_messages_to_user_only(request.messages)
        llm_messages = [strategist_system_prompt] + filtered_messages

        # If no messages after filtering, add the prompt as a user message
        if not filtered_messages:
            llm_messages.append({"role": "user", "content": str(request.prompt)})

        try:
            # Use unified fallback system through self.acompletion
            response = await self.acompletion(
                model=model,
                messages=llm_messages,
                response_format=ContentStrategy,
                user=request.user,
                provider_chain=provider_chain,
                metadata=create_langfuse_metadata(
                    agent_type=AgentType.CONTENT_WRITER,
                    step="generate_content_strategy",
                    generation_name="content_strategy_generator",
                    user_id=request.user,
                    trace_name="longwriter_workflow",
                    additional_metadata={
                        "prompt_length": len(str(request.prompt)),
                    },
                    tags=[
                        "agent:content_writer",
                        "step:strategy",
                        "longwriter",
                    ],
                ),
            )

            # Update cost tracker with response usage
            if hasattr(response, "usage"):
                conversation_ctx.cost_tracker.update_prompt_tokens(
                    response.usage.prompt_tokens
                )
                conversation_ctx.cost_tracker.update_completion_tokens(
                    response.usage.completion_tokens
                )

            # Get the content from the response
            content = response.choices[0].message.content
            logging.debug(f"\033[97;40mContent Strategy Response:\n{content}\033[0m")

            strategy = ContentStrategy.model_validate_json(content)
            # Add the original messages and user ID to the strategy object
            strategy.original_messages = request.messages
            strategy.user = request.user  # Set the user ID
            return strategy

        except Exception as e:
            logging.error(f"\033[91mError creating content strategy: {str(e)}\033[0m")
            raise

    async def get_html_strategy(
        self,
        allowed_html_tags: str,
        allowed_html_classes: str = "",
        content_strategy: ContentStrategy = None,
        model: str = None,
        provider_chain: ProviderChain = None,
        conversation_ctx: ConversationContext = None,
    ) -> HTMLTagStrategy:
        # Use original provider chain to preserve failed state from routing
        if provider_chain:
            logging.debug(f"HTML_STRATEGY: Using ProviderChain: {provider_chain}")
            logging.debug(
                f"HTML_STRATEGY: Using original chain to preserve failed state from routing"
            )
            # Reset current_index to allow trying all non-failed providers for this method
            provider_chain.current_index = 0
            logging.debug(
                f"HTML_STRATEGY: Reset current_index to 0 for fresh method attempt"
            )
        logging.info(f"Making completion call for HTML strategy using model: {model}")
        # First check if model supports response schema
        # Commented cos not reliable (e.g. mistral-medium)
        # if not supports_function_calling(model=model):
        #     raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

        html_strategist_prompt = f"""
        You are an HTML strategist. Given a constraint of allowed HTML tags, allowed HTML classes, and a content strategy, 
        provide a list of HTML tags and classes that would be most effective for structuring the content.
        IMPORTANT: We are only writing the body of the content, do not include navigation, footer, sidebars, and other peripheral content.
        
        For dynamic content like landing pages, homepages, and marketing content, consider using these Bootstrap classes for visual impact, use them where appropriate, use them sparingly, and do not overuse them:
        Background classes: bg-primary, bg-primary-subtle, bg-secondary, bg-secondary-subtle, bg-light, bg-light-subtle, bg-dark, bg-dark-subtle, bg-body-secondary, bg-body-tertiary, bg-body, bg-black, bg-white, bg-transparent
        Text classes: text-primary, text-primary-emphasis, text-secondary, text-secondary-emphasis, text-light, text-light-emphasis, text-dark, text-dark-emphasis, text-body, text-body-emphasis, text-body-secondary, text-body-tertiary, text-black, text-white
        Button classes: btn, btn-primary, btn-secondary, btn-success, btn-danger, btn-warning, btn-info, btn-light, btn-dark, btn-link, btn-outline-primary, btn-outline-secondary, btn-outline-success, btn-outline-danger, btn-outline-warning, btn-outline-info, btn-outline-light, btn-outline-dark, btn-lg, btn-sm, w-100
        """

        try:
            # Use unified fallback system through self.acompletion
            response = await self.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": dedent(html_strategist_prompt)},
                    {
                        "role": "user",
                        "content": f"Allowed HTML tags: {allowed_html_tags}\nAllowed HTML classes: {allowed_html_classes}\nContent strategy: {content_strategy.model_dump_json()}",
                    },
                ],
                response_format=HTMLTagStrategy,
                user=content_strategy.user,
                provider_chain=provider_chain,
                metadata=create_langfuse_metadata(
                    agent_type=AgentType.CONTENT_WRITER,
                    step="generate_html_strategy",
                    generation_name="html_strategy_generator",
                    user_id=content_strategy.user,
                    parent_observation_id=observation_manager.get_observation_id(
                        ObservationName.LONGWRITER
                    ),
                    additional_metadata={
                        "html_formatting_options_count": (
                            len(allowed_html_tags.split(","))
                            if allowed_html_tags
                            else 0
                        ),
                    },
                    tags=[
                        "agent:content_writer",
                        "step:html_strategy",
                        "longwriter",
                    ],
                ),
            )

            # Update cost tracker with response usage
            if hasattr(response, "usage") and conversation_ctx:
                conversation_ctx.cost_tracker.update_prompt_tokens(
                    response.usage.prompt_tokens
                )
                conversation_ctx.cost_tracker.update_completion_tokens(
                    response.usage.completion_tokens
                )

            # Get the content from the response
            content = response.choices[0].message.content
            logging.debug(f"\033[97;44mHTML Strategy Response:\n{content}\033[0m")

            return HTMLTagStrategy.model_validate_json(content)

        except Exception as e:
            logging.error(f"\033[91mError creating HTML strategy: {str(e)}\033[0m")
            raise

    async def get_content_outline(
        self,
        content_strategy: ContentStrategy,
        html_strategy: HTMLTagStrategy,
        model: str,
        provider_chain: ProviderChain = None,
        conversation_ctx: ConversationContext = None,
    ) -> ContentOutline:
        # Use original provider chain to preserve failed state from routing
        if provider_chain:
            logging.debug(f"CONTENT_OUTLINE: Using ProviderChain: {provider_chain}")
            logging.debug(
                f"CONTENT_OUTLINE: Using original chain to preserve failed state from routing"
            )
            # Reset current_index to allow trying all non-failed providers for this method
            provider_chain.current_index = 0
            logging.debug(
                f"CONTENT_OUTLINE: Reset current_index to 0 for fresh method attempt"
            )
        logging.info(f"Making completion call for content outline using model: {model}")
        # First check if model supports response schema
        # Commented cos not reliable (e.g. mistral-medium)
        # if not supports_function_calling(model=model):
        #     raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

        content_outliner_prompt = """
        You are a creative content outliner. Given a content strategy and HTML tag strategy, 
        create a detailed, structured outline for the content.
        
        Requirements:
        1. The sum of all section word counts should be within 10% of the total recommended word count
        2. Be creative and avoid archaic structures unless appropriate
        3. Each section should have clear, actionable content ideas
        4. Include multimedia suggestions based on available HTML tags
        5. For each section, specify an appropriate section_type that matches the content and purpose
        
        Section Type Guidelines:
        Choose appropriate section_type values that fit the subject and content type being created.
        Common section types include: Hero, Title, Subtitle, Introduction, Body, Subsection, Conclusion, 
        Media, Pullquote, Sidebar, Feature List, Benefits, Social Proof, Testimonials, Pricing, 
        Call to Action, FAQ, Resources, Contact. Don't limit yourself to these; use any section type
        that fits your content strategy.
        
        Important: The section_type should guide the content writer's approach:
        - Hero sections should be concise and compelling (20-50 words typically)
        - Title/Subtitle sections should be brief and impactful
        - Introduction sections set context (50-150 words)
        - Body sections contain main content (200+ words)
        - Call to Action sections should be focused and brief (20-100 words)
        - Choose types that make sense for your specific content strategy
        """

        try:
            # Use unified fallback system through self.acompletion
            response = await self.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": dedent(content_outliner_prompt)},
                    {
                        "role": "user",
                        "content": f"Content strategy: {content_strategy.model_dump_json()}\nHTML strategy: {html_strategy.model_dump_json()}",
                    },
                ],
                response_format=ContentOutline,
                user=content_strategy.user,
                provider_chain=provider_chain,
                metadata=create_langfuse_metadata(
                    agent_type=AgentType.CONTENT_WRITER,
                    step="generate_content_outline",
                    generation_name="content_outline_generator",
                    user_id=content_strategy.user,
                    parent_observation_id=observation_manager.get_observation_id(
                        ObservationName.LONGWRITER
                    ),
                    additional_metadata={
                        "content_structure_sections_planned": (
                            content_strategy.sections_count
                            if hasattr(content_strategy, "sections_count")
                            else None
                        ),
                    },
                    tags=[
                        "agent:content_writer",
                        "step:outline",
                        "longwriter",
                    ],
                ),
            )

            # Update cost tracker with response usage
            if hasattr(response, "usage") and conversation_ctx:
                conversation_ctx.cost_tracker.update_prompt_tokens(
                    response.usage.prompt_tokens
                )
                conversation_ctx.cost_tracker.update_completion_tokens(
                    response.usage.completion_tokens
                )

            # Get the content from the response
            content = response.choices[0].message.content
            logging.debug(f"\033[97;45mContent Outline Response:\n{content}\033[0m")

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
        chunk_size: int | None = None,
        provider_chain: ProviderChain = None,
        conversation_ctx: ConversationContext = None,
    ) -> AsyncGenerator:
        # Use original provider chain to preserve failed state from routing
        if provider_chain:
            logging.debug(f"CONTENT_DRAFT: Received ProviderChain: {provider_chain}")
            logging.debug(
                f"CONTENT_DRAFT: Using original chain to preserve failed state from routing"
            )
            # Reset current_index to allow trying all non-failed providers for this method
            provider_chain.current_index = 0
            logging.debug(
                f"CONTENT_DRAFT: Reset current_index to 0 for fresh method attempt"
            )
        logging.info(
            f"Making streaming completion call for content draft section '{section.title}' using model: {model}"
        )

        # --- Step 1: Extract Search Results (if available) ---
        extracted_search_results: Optional[str] = None
        if (
            hasattr(content_strategy, "original_messages")
            and content_strategy.original_messages
        ):
            # Find the last user message in the original context
            last_user_msg_content = None
            for msg in reversed(content_strategy.original_messages):
                if msg.get("role") == "user":
                    last_user_msg_content = msg.get("content")
                    break

            if last_user_msg_content:
                # Use regex to find search results within the content
                search_match = re.search(
                    r"<web_search_results>(.*?)</web_search_results>",
                    last_user_msg_content,
                    re.DOTALL,
                )
                if search_match:
                    extracted_search_results = search_match.group(1).strip()
                    logging.info(
                        f"Extracted web search results for section '{section.title}'"
                    )
                else:
                    logging.info(
                        f"No <web_search_results> tag found in last user message for section '{section.title}'"
                    )
            else:
                logging.info(
                    f"No user message found in original_messages for section '{section.title}'"
                )
        else:
            logging.info(
                f"No original_messages found in content_strategy for section '{section.title}'"
            )

        # --- Step 2: Construct Prompt with Optional Search Context ---
        # Extract tone and image instructions as before
        tone_instruction = ""
        image_instructions = None
        if (
            hasattr(content_strategy, "original_messages")
            and content_strategy.original_messages
        ):
            messages = content_strategy.original_messages
            if messages and messages[0]["role"] == "system":
                # import re # Already imported at file level likely, but ensure it's available
                tone_match = re.search(
                    r"<TONE>(.*?)</TONE>", messages[0]["content"], re.DOTALL
                )
                if tone_match:
                    tone = tone_match.group(1).strip()
                    tone_instruction = f"\n11. Use this specific tone of voice:\n{tone}"  # Adjusted index

                image_match = re.search(
                    r"<IMAGE_HANDLING>(.*?)</IMAGE_HANDLING>",
                    messages[0]["content"],
                    re.DOTALL,
                )
                if image_match:
                    image_instructions = image_match.group(1).strip()

        # Create the search context section string conditionally
        search_context_section = ""
        if extracted_search_results:
            search_context_section = f"""

        Context from Recent Web Search (Use this information where relevant):
        --- START SEARCH RESULTS ---
        {extracted_search_results}
        --- END SEARCH RESULTS ---
        """

        # Define the main prompt using an f-string, injecting the search context
        content_writer_prompt = f"""
        You are a creative content writer. Write the next section of content based on the given outline and strategy.
        This section is part of a larger article, so ensure continuity with previous sections.

        Key points:
        1. Use ONLY these HTML tags: {", ".join(html_strategy.tags)}
        2. Use ONLY these HTML classes: {", ".join(html_strategy.classes) if html_strategy.classes else "no specific classes recommended"}
        3. Do NOT use <!DOCTYPE>, <html>, <head>, or <body> tags
        4. Start directly with content using allowed tags
        5. Follow the content strategy and address key questions
        6. Aim for {section.target_word_count} words
        7. Be creative and engaging
        8. Ensure continuity with previous sections, avoid repetitive phrases
        9. Keep in mind the overall structure of the article as outlined
        10. Do NOT use any markdown formatting (no *, _, #, -,``` etc.)
        11. Only use the specified HTML tags and classes for formatting{tone_instruction}
        {search_context_section}
        """  # End of main instruction block

        # Append image requirements if needed
        if "img" in html_strategy.tags:
            if image_instructions:
                content_writer_prompt += f"""
        Image Requirements:
        {image_instructions}
        """
            else:
                # Default image instructions
                content_writer_prompt += """
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
        """

        # Append the outline and final instruction
        content_writer_prompt += f"""
        Here's the outline of the entire article:
        {outline.model_dump_json()}

        Now, write the next section: {section.title}
        Remember to write content appropriate for the section type and target word count specified in the section details.
        """

        # Initialize or update chat history (using the fully constructed prompt)
        # CONTAMINATION FIX: Use conversation context instead of instance state
        if not conversation_ctx:
            conversation_ctx = ConversationContext(user_id=str(content_strategy.user))

        if conversation_ctx.content_writer_messages is None:
            conversation_ctx.content_writer_messages = [
                {"role": "system", "content": content_writer_prompt},
                {
                    "role": "user",
                    "content": f"Write next section:\n{section.model_dump_json()}",
                },
            ]
        else:
            # For subsequent sections, use the same format
            conversation_ctx.content_writer_messages.append(
                {
                    "role": "user",
                    "content": f"Write next section:\n{section.model_dump_json()}",
                }
            )

        # --- Rest of the function (LLM call, streaming, history update) updated for unified fallback ---
        # Use provided chunk_size or default from config
        effective_chunk_size = chunk_size or self.default_chunk_size

        # Use unified fallback system through self.acompletion with streaming
        response = await self.acompletion(
            model=model,
            messages=conversation_ctx.content_writer_messages,
            stream=True,
            user=content_strategy.user,
            provider_chain=provider_chain,
            metadata=create_langfuse_metadata(
                agent_type=AgentType.CONTENT_WRITER,
                step="write_content",
                generation_name="content_writer_streaming",
                user_id=content_strategy.user,
                parent_observation_id=observation_manager.get_observation_id(
                    "longwriter"
                ),
                additional_metadata={
                    "planned_content_length": content_strategy.recommended_word_count,
                    "content_sections_total": (len(outline.sections) if outline else 0),
                    "streaming_chunk_size": effective_chunk_size,
                    "is_streaming_response": True,
                },
                tags=[
                    "agent:content_writer",
                    "step:write",
                    "longwriter",
                    "streaming",
                    f"planned_length:{content_strategy.recommended_word_count}",
                ],
            ),
        )

        accumulator = TokenAccumulator(chunk_size=effective_chunk_size)
        full_response = ""

        async for chunk in response:
            if chunk.choices[0].delta.content is not None:
                token = chunk.choices[0].delta.content
                full_response += token
                accumulated, token_count = await accumulator.add_token(token)
                if accumulated:
                    yield accumulated, token_count

        # Add the complete response to message history
        conversation_ctx.content_writer_messages.append(
            {"role": "assistant", "content": full_response}
        )

        # Flush any remaining tokens
        final_chunk, final_count = await accumulator.flush()
        if final_chunk:
            yield final_chunk, final_count

    async def content_creation_agent(
        self, request: ContentRequest, model: str, provider_chain: ProviderChain = None
    ):
        """Main content generation method that coordinates the content creation process."""
        # Ensure user ID is present in the request
        logging.info("CONTENT_CREATION_AGENT: Starting content creation agent")
        if not hasattr(request, "user") or not request.user:
            error_msg = "CRITICAL: No user ID provided in content creation request. Every request must be associated with a user."
            logging.error(error_msg, exc_info=True)
            raise ValueError(error_msg)

        try:
            user_id = int(request.user)  # Validate user ID is a valid integer
        except (ValueError, TypeError):
            error_msg = (
                "CRITICAL: Invalid user ID format. User ID must be a valid integer."
            )
            logging.error(error_msg, exc_info=True)
            raise ValueError(error_msg)

        # CONTAMINATION FIX: Create isolated conversation context for this request
        conversation_ctx = ConversationContext(user_id=str(user_id))
        logging.info(
            f"CONTAMINATION_FIX: Created isolated conversation context: {conversation_ctx}"
        )

        content_strategy = await self.get_content_strategy(
            request, model, provider_chain, conversation_ctx
        )

        # Instead of yielding formatted strings, yield tuples with special message type
        # Create a JSON string for the disclosure
        from kavya.models import create_cost_disclosure_dict

        disclosure_json = json.dumps(
            create_cost_disclosure_dict(
                prompt_tokens=conversation_ctx.cost_tracker.prompt_tokens,
                completion_tokens=conversation_ctx.cost_tracker.completion_tokens,
                description="Content strategy generation",
            )
        )
        # Yield as a tuple (content, token_count) to maintain consistent format
        yield disclosure_json, 0

        html_strategy = await self.get_html_strategy(
            request.allowed_html_tags,
            request.allowed_html_classes,
            content_strategy,
            model,
            provider_chain,
            conversation_ctx,
        )

        # Disclose HTML strategy costs - using tuple format
        disclosure_json = json.dumps(
            create_cost_disclosure_dict(
                prompt_tokens=conversation_ctx.cost_tracker.prompt_tokens,
                completion_tokens=conversation_ctx.cost_tracker.completion_tokens,
                description="HTML strategy generation",
            )
        )
        yield disclosure_json, 0

        content_outline = await self.get_content_outline(
            content_strategy, html_strategy, model, provider_chain, conversation_ctx
        )

        # Disclose content outline costs - using tuple format
        disclosure_json = json.dumps(
            create_cost_disclosure_dict(
                prompt_tokens=conversation_ctx.cost_tracker.prompt_tokens,
                completion_tokens=conversation_ctx.cost_tracker.completion_tokens,
                description="Content outline generation",
            )
        )
        yield disclosure_json, 0

        for section in content_outline.sections:
            async for token, token_count in self.get_content_draft(
                section,
                content_strategy,
                html_strategy,
                content_outline,
                model,
                self.default_chunk_size,
                provider_chain,
                conversation_ctx,
            ):
                yield token, token_count

        # Clean up the longwriter observation when workflow completes
        observation_manager.clear_observation(ObservationName.LONGWRITER)


class Controllers:
    def __init__(self, **kwargs):
        self.controllers = {}

        # Require config with general settings
        config = kwargs.get("config")
        if not config or "general_settings" not in config:
            raise ValueError(
                "Config must include general_settings with basic_router_max_chars"
            )

        if "basic_router_max_chars" not in config["general_settings"]:
            raise ValueError("general_settings must include basic_router_max_chars")

        self.basic_router_max_chars = config["general_settings"][
            "basic_router_max_chars"
        ]

        # Load controller configuration
        controller_config = config["controller"]  # hard fail if missing
        self.longwriter_word_threshold = controller_config[
            "longwriter_word_threshold"
        ]  # hard fail if missing
        self.longwriter_only_args = controller_config["longwriter_only_args"]

        self.create_controller("default", **kwargs)

    def create_controller(self, id, **kwargs):
        # getting kwargs
        config = kwargs.get("config", None)
        model = kwargs.get("model", None)
        api_base = kwargs.get("api_base", None)
        api_key = kwargs.get("api_key", None)
        progress_bar = kwargs.get("progress_bar", None)

        if id.startswith("longwriter"):
            # initializing longwriter
            controller = Longwriter(
                config=config,
                model=model,
                api_base=api_base,
                api_key=api_key,
                progress_bar=progress_bar,
            )
        else:
            # initializing controller
            controller = Controller(
                config=config,
                model=model,
                api_base=api_base,
                api_key=api_key,
                progress_bar=progress_bar,
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

    async def response(
        self,
        request: ChatCompletionRequest,
        id,
        amethod_name,
        use_research_agent=False,
        **kwargs,
    ):
        logging.info(
            f"DEBUG: response method called with id: {id}, method: {amethod_name}"
        )
        logging.info(f"DEBUG: request model: {request.model}")
        logging.info(f"DEBUG: kwargs: {kwargs}")

        # Dump request to see all parameters
        request_dump = request.model_dump(
            exclude=self.longwriter_only_args, exclude_none=True
        )
        logging.info(f"DEBUG: request dump: {request_dump}")

        response, search_results = await self.controllers[id].__getattribute__(
            amethod_name
        )(
            use_research_agent,
            **{
                k: v
                for k, v in request.model_dump(
                    exclude=self.longwriter_only_args, exclude_none=True
                ).items()
                if k not in kwargs and k not in ["router_usage"]
            },
            **kwargs,
        )

        return response, search_results

    async def basic_routing(
        self,
        request: ChatCompletionRequest,
        cost_tracker: Optional[RequestCostTracker] = None,
        provider_chain: Optional[ProviderChain] = None,
    ):
        # Longwriter only supports streaming requests, so force non-streaming to use completion
        if not request.stream:
            logging.info(
                "Non-streaming request detected, skipping routing analysis and using completion controller"
            )
            return "completion"

        logging.info("Making completion call for routing analysis")

        # Get request data for processing
        request_data = request.model_dump()

        # Ensure user ID is present in the request
        user_id = request_data.get("user")
        if not user_id:
            error_msg = "CRITICAL: No user ID provided in request. Every request must be associated with a user."
            logging.error(error_msg, exc_info=True)
            raise ValueError(error_msg)

        # Get the default controller to use its methods
        default_controller = self.controllers["completion"]

        # First determine which model to use
        routed_model = default_controller.get_model(**request_data)
        # Store the model in the request for later use
        request.model = routed_model

        # Get longwriter word threshold from request or use config default
        word_threshold = (
            getattr(request, "longwriter_word_threshold", None)
            or self.longwriter_word_threshold
        )

        # Trim long prompts for basic router analysis
        prompt = request_data["messages"][-1]["content"]
        if len(prompt) > self.basic_router_max_chars:
            start = prompt[: self.basic_router_max_chars // 3]  # Keep first third
            end = prompt[-self.basic_router_max_chars // 3 :]  # Keep last third
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
        logging.info(
            f"DEBUG : Model used by default controller {default_controller.model}"
        )
        routing_request = ChatCompletionRequest(
            model=default_controller.model,
            messages=[
                {
                    "role": "system",
                    "content": f"""You are a routing analyzer that evaluates if content requires Longwriter's capabilities.
Analyze the prompt and return a JSON object that exactly matches this Pydantic model:

{RoutingAnalysis.model_json_schema()}
{xml_guidance}

When analyzing the prompt, set these fields accurately:

1. length_score: Estimate the probability (0.0-1.0) that the response will exceed {word_threshold} words based on the prompt's requirements.

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
  * Would be improved by hierarchical organization""",
                },
                {"role": "user", "content": "Analyze this prompt: " + prompt},
            ],
            stream=False,  # Force non-streaming for routing
            response_format=RoutingAnalysis,
            user=request_data.get("user"),  # Pass through the user ID
        )

        # Use the ProviderChain system for routing decision to maintain failed state
        if provider_chain:
            logging.debug(f"ROUTING: Using ProviderChain for routing: {provider_chain}")
            # Create fresh copy of provider chain to avoid consumption issues during routing
            routing_provider_chain = provider_chain.create_fresh_copy()
            # Use controller's acompletion method which respects ProviderChain
            response = await default_controller.acompletion(
                model=default_controller.model,  # Use model for routing decision
                messages=routing_request.messages,
                response_format=RoutingAnalysis,
                user=routing_request.user,  # Pass through the user ID
                provider_chain=routing_provider_chain,
                metadata=create_langfuse_metadata(
                    agent_type=AgentType.ROUTER,
                    step="route_to_controller",
                    generation_name="controller_router",
                    user_id=routing_request.user,
                    trace_name="routing_workflow",
                    additional_metadata={
                        "decision_making_model": default_controller.model,
                        "controller_options_available": list(self.controllers.keys()),
                    },
                    tags=[
                        "agent:router",
                        "routing",
                        f"controllers:{len(self.controllers)}",
                    ],
                ),
            )

            # After routing analysis, update the original provider_chain with any failures from routing
            # This ensures longwriter methods inherit the failed state from routing
            if routing_provider_chain.failed:
                logging.debug(
                    f"ROUTING: Updating original chain with routing failures: {routing_provider_chain.failed}"
                )
                for failed_provider in routing_provider_chain.failed:
                    if failed_provider not in provider_chain.failed:
                        provider_chain.failed.append(failed_provider)
                        logging.debug(
                            f"ROUTING: Added {failed_provider} to original chain failed list"
                        )
        else:
            # Fallback to direct acompletion if no ProviderChain
            logging.warning("ROUTING: No ProviderChain found, using direct acompletion")
            response = await acompletion(
                model=default_controller.model,  # Use model for routing decision
                messages=routing_request.messages,
                api_base=default_controller.api_base,
                api_key=default_controller.api_key,
                response_format=RoutingAnalysis,
                user=routing_request.user,  # Pass through the user ID
                metadata=create_langfuse_metadata(
                    agent_type=AgentType.ROUTER,
                    step="route_to_controller",
                    generation_name="controller_router_fallback",
                    user_id=routing_request.user,
                    trace_name="routing_workflow",
                    additional_metadata={
                        "decision_making_model": default_controller.model,
                        "controller_options_available": list(self.controllers.keys()),
                    },
                    tags=[
                        "agent:router",
                        "routing",
                        "fallback",
                        f"controllers:{len(self.controllers)}",
                    ],
                ),
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
        logging.info(
            format_usage_log(
                prompt_preview=prompt_preview,
                usage_info=usage_info,
                cost=cost,
                user_id=int(routing_request.user) if routing_request.user else None,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
        )

        # Store router usage in the request object
        request.router_usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }

        try:
            # Get the content directly from the response
            content = response.choices[0].message.content

            # Check if content is None or empty before trying to parse
            if not content:
                logging.error(
                    "Error: Empty or None content received from routing analysis"
                )
                return "completion"

            analysis = RoutingAnalysis.model_validate_json(content)

            # Add debug output for routing decision
            logging.debug("\033[95m=== Routing Analysis ===\033[0m")
            logging.debug(f"\033[94mWord Threshold: {word_threshold}\033[0m")
            logging.debug(f"\033[94mLength Score: {analysis.length_score}\033[0m")
            logging.debug(f"\033[94mNeeds Structure: {analysis.needs_structure}\033[0m")
            logging.debug(f"\033[94mIs Data Dump: {analysis.is_data_dump}\033[0m")

            # Route to longwriter if:
            # 1. Content will be long (> {word_threshold} words)
            # 2. Content benefits from structure
            # 3. Not just a data dump
            use_longwriter = (
                analysis.length_score > 0.7
                and analysis.needs_structure
                and not analysis.is_data_dump
            )

            logging.debug(
                f"\033[94mDecision: {'Using Longwriter' if use_longwriter else 'Using Standard Completion'}\033[0m"
            )
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


def update_token_usage(
    user_id: Optional[int],
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
) -> None:
    """Update token usage in the database. Raises RuntimeError if update fails."""
    if (
        user_id is not None
        and prompt_tokens is not None
        and completion_tokens is not None
    ):
        try:
            from kavya.openai_server import app

            if not hasattr(app, "db") or not app.db:
                error_msg = "CRITICAL: Database connection not available. Cannot proceed without updating token usage."
                logging.error(error_msg, exc_info=True)
                raise RuntimeError(error_msg)
            logging.info(
                "DEBUG : Updating cost in database from controllers.update_token_usage"
            )
            app.db.update_usage_with_response(
                account_id=user_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception as db_error:
            error_msg = (
                f"CRITICAL: Failed to update token usage in database: {str(db_error)}"
            )
            logging.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from db_error


def format_usage_log(
    prompt_preview: str,
    usage_info: str,
    cost: Optional[float] = None,
    user_id: Optional[int] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
) -> str:
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
    cleaned = " ".join(prompt.split())
    return cleaned[:max_length] + ("..." if len(cleaned) > max_length else "")


def format_total_cost_log(total_cost: float) -> str:
    return f"\033[95m=== Total Request Cost: ${total_cost:.6f} ===\033[0m"


def custom_cost_usage_callback(
    kwargs,  # kwargs to completion
    completion_response,  # response from completion
    start_time,
    end_time,  # start/end time
):
    # Get user ID from kwargs - hard fail if not present
    user = kwargs.get("user", None)
    if not user:
        error_msg = "CRITICAL: No user ID provided in callback. Every request must be billed to a user."
        logging.critical(
            error_msg
        )  # Changed from error to critical for proper severity
        raise ValueError(error_msg)

    messages = kwargs.get("messages", [])
    prompt_preview = (
        format_prompt_preview(messages[-1]["content"]) if messages else "No prompt"
    )
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

            logging.info(
                format_usage_log(  # Using info level for usage logs
                    prompt_preview=prompt_preview,
                    usage_info=usage_info,
                    cost=cost,
                    user_id=int(user),
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                )
            )
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

        logging.info(
            format_usage_log(  # Using info level for usage logs
                prompt_preview=prompt_preview,
                usage_info=usage_info,
                cost=cost,
                user_id=int(user),
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
            )
        )


# Register the callback
litellm.success_callback = [custom_cost_usage_callback]
logging.info("Registered custom_cost_usage_callback with litellm")


# Example usage for non-streaming completion with proper Langfuse integration
def make_non_streaming_completion(prompt, user_id="example_user"):
    response = litellm.completion(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        user=user_id,
        metadata={
            "trace_user_id": user_id,
            "generation_name": "example_non_streaming",
            "tags": ["example", "non_streaming"],
        },
    )
    return response


# Example usage for streaming completion with proper Langfuse integration
def make_streaming_completion(prompt, user_id="example_user"):
    response = litellm.completion(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        stream_options={
            "include_usage": True
        },  # Important for getting usage info in streaming
        user=user_id,
        metadata={
            "trace_user_id": user_id,
            "generation_name": "example_streaming",
            "tags": ["example", "streaming"],
        },
    )

    # Process streaming response
    collected_content = []
    for chunk in response:
        if chunk.choices[0].delta.content:
            collected_content.append(chunk.choices[0].delta.content)

    return "".join(collected_content)
