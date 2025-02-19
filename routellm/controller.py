import warnings
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional, Callable, AsyncGenerator, List, Tuple

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

from routellm.models import (
    ChatCompletionRequest, ContentRequest, ContentStrategy, 
    HTMLTagStrategy, OutlineSection, ContentOutline, 
    ContentDraft, FullContent, RoutingAnalysis
)
from routellm.routers.routers import ROUTER_CLS
from pydantic import BaseModel

# Model translation mapping
def get_model_translations(config):
    """Get model translations from config, with fallback to defaults."""
    if config and "model_translations" in config:
        return config["model_translations"]
    # Default fallback if not in config
    return {
        "kavya-m1": "router-mf-0.1",
        "kavya-m1-eu": "router-mf-0.1"
    }

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
LONGWRITER_ONLY_ARGS = ["allowed_html_tags"]

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
        self.model_pair = ModelPair(strong=strong_model, weak=weak_model)
        self.routers = {}
        self.api_base = api_base
        self.api_key = api_key
        self.model_counts = defaultdict(lambda: defaultdict(int))
        self.progress_bar = progress_bar
        self.model_translations = get_model_translations(config)

        if config is None:
            config = GPT_4_AUGMENTED_CONFIG


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
        self.suppress_warnings = suppress_warnings

        self.predefined_prompts = self.load_predefined_prompts()

    def load_predefined_prompts(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, 'predefined_prompts.json')
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except:
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

    async def acompletion(
        self,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        **kwargs,
    ):
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
        logging.info(f"Making async {'streaming' if kwargs.get('stream') else 'non-streaming'} completion call using model: {model}")
        logging.debug("acontroller function started")
        
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
        
        # Keep the existing warning suppression logic
        if self.suppress_warnings:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                logging.debug("last sprint")
                response = await acompletion(api_base=self.api_base, api_key=self.api_key, **kwargs)
        else:
            logging.debug("last sprint 2")
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
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.content_writer_messages = None  # Will be initialized during first content draft
        self.cost_tracker = RequestCostTracker()  # Initialize cost tracker
        self.user = None  # Will be set during API calls
    
    async def get_content_strategy(self, request: ContentRequest, model: str) -> ContentStrategy:
        logging.info(f"Making completion call for content strategy using model: {model}")
        # Reset cost tracker for new request
        self.cost_tracker = RequestCostTracker()
        self.user = request.user
        # First check if model supports response schema
        if not supports_function_calling(model=model):
            raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

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

    async def get_html_strategy(self, allowed_html_tags: str, content_strategy: ContentStrategy, model: str) -> HTMLTagStrategy:
        logging.info(f"Making completion call for HTML strategy using model: {model}")
        # First check if model supports response schema
        if not supports_function_calling(model=model):
            raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

        html_strategist_prompt = f'''
        You are an HTML strategist. Given a list of allowed HTML tags and a content strategy, 
        provide a list of HTML tags that would be most effective for structuring the content.
        '''
        
        try:
            response = await acompletion(
                model=model,  # Direct model use after routing decision
                api_base=self.api_base,
                api_key=self.api_key,
                messages=[
                    {"role": "system", "content": dedent(html_strategist_prompt)},
                    {"role": "user", "content": f"Allowed HTML tags: {allowed_html_tags}\nContent strategy: {content_strategy.model_dump_json()}"}
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
        if not supports_function_calling(model=model):
            raise ValueError(f"Model {model} does not support structured output (response_schema). Longwriter requires a model that supports structured output.")

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
                {"role": "user", "content": f"Section to write: {section.model_dump_json()}\nStrategy: {content_strategy.model_dump_json()}"}
            ]
        else:
            # For subsequent sections, add to existing conversation
            self.content_writer_messages.append(
                {"role": "user", "content": f"Section to write: {section.model_dump_json()}\nStrategy: {content_strategy.model_dump_json()}"}
            )

        # Use acompletion for async streaming
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
        # Disclose content strategy costs
        disclosure = routellm.models.create_cost_disclosure_dict(
            prompt_tokens=self.cost_tracker.prompt_tokens,
            completion_tokens=self.cost_tracker.completion_tokens,
            description='Content strategy generation'
        )
        yield f"data: {json.dumps(disclosure)}\n\n"
        
        html_strategy = await self.get_html_strategy(request.allowed_html_tags, content_strategy, model)
        # Disclose HTML strategy costs
        disclosure = routellm.models.create_cost_disclosure_dict(
            prompt_tokens=self.cost_tracker.prompt_tokens,
            completion_tokens=self.cost_tracker.completion_tokens,
            description='HTML strategy generation'
        )
        yield f"data: {json.dumps(disclosure)}\n\n"
        
        content_outline = await self.get_content_outline(content_strategy, html_strategy, model)
        # Disclose content outline costs
        disclosure = routellm.models.create_cost_disclosure_dict(
            prompt_tokens=self.cost_tracker.prompt_tokens,
            completion_tokens=self.cost_tracker.completion_tokens,
            description='Content outline generation'
        )
        yield f"data: {json.dumps(disclosure)}\n\n"
        
        for section in content_outline.sections:
            async for token, token_count in self.get_content_draft(
                section,
                content_strategy,
                html_strategy,
                content_outline,
                model
            ):
                yield token, token_count

    async def acompletion(
        self,
        *,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        **kwargs,
    ):
        """Override of base acompletion to handle longwriter-specific streaming."""
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
        
        request = ContentRequest(
            prompt=kwargs["messages"][-1]["content"],
            allowed_html_tags=kwargs.get("allowed_html_tags", "")
        )

        async for token, token_count in self.content_creation_agent(request, kwargs["model"]):
            yield token, token_count

class Controllers:
    def __init__(self, **kwargs):
        self.controllers = {}
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
        return await self.controllers[id].__getattribute__(amethod_name)(
            **request.model_dump(exclude=LONGWRITER_ONLY_ARGS, exclude_none=True),
            **kwargs
        )

    async def basic_routing(self, request: ChatCompletionRequest, cost_tracker: Optional[RequestCostTracker] = None):
        logging.info("Making completion call for routing analysis using weak model")
        
        # Ensure user ID is present in the request
        user_id = request.model_dump().get("user")
        if not user_id:
            error_msg = "CRITICAL: No user ID provided in request. Every request must be associated with a user."
            logging.error(error_msg)
            raise ValueError(error_msg)
            
        # Get the default controller to use its methods
        default_controller = self.controllers["default"]
        
        # First determine which model to use based on router if specified
        routed_model = None
        if "model" in request.model_dump():
            parsed_router, parsed_threshold = default_controller._parse_model_name(request.model)
            if parsed_router and parsed_threshold:
                default_controller._validate_router_threshold(parsed_router, parsed_threshold)
                routed_model = default_controller._get_routed_model_for_completion(
                    request.messages, parsed_router, parsed_threshold
                )
                # Store the routed model in the request for later use
                request.model = routed_model

        # Check both length and content type requirements
        routing_request = ChatCompletionRequest(
            model=default_controller.model_pair.weak,  # Always use weak model for checks
            messages=[
                {
                    "role": "system",
                    "content": f"""You are a routing analyzer that evaluates if content requires Longwriter's capabilities.
Analyze the prompt and return a JSON object that exactly matches this Pydantic model:

{RoutingAnalysis.model_json_schema()}"""
                },
                {
                    "role": "user",
                    "content": "Analyze this prompt: " + request.messages[-1]["content"]
                }
            ],
            stream=False,  # Force non-streaming for routing
            response_format=RoutingAnalysis,
            user=request.model_dump().get("user")  # Pass through the user ID
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
                analysis.length_score > 0.8 and 
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