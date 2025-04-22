import time
import json
import shortuuid
import logging
import sys
from datetime import datetime
import re

from pydantic import BaseModel, Field, field_validator
from typing import AsyncGenerator, Dict, List, Literal, Optional, Union, Any
from litellm import CustomStreamWrapper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# create status response https://spec.modelcontextprotocol.io/specification/basic/utilities/progress/
def create_status_response_dict(status: str, step : int, total : int, phase : str) -> Dict[str, Any]:
    logging.debug("create_status_response")
    return {
            "jsonrpc": "2.0",
            "method": "agent/status",
            "params": {
                "status": status,
                "step": step,
                "total_steps": total,
                "phase": phase
            }
        }

def create_cost_disclosure_dict(prompt_tokens: int, completion_tokens: int, description: str) -> Dict[str, Any]:
    """Create a cost disclosure message for background operations."""
    return {
            "jsonrpc": "2.0",
            "method": "agent/cost_disclosure",
            "params": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "description": description
            }
        }

def create_chunk_response(token: str, response_id: str, created_time: int, model: str, controller=None) -> Dict[str, Any]:
    """
    Create a formatted chunk response for streaming API.
    
    Args:
        token: The content token to stream
        response_id: The unique ID for this response
        created_time: Unix timestamp when the response was created
        model: The model name
        controller: Optional controller object with usage tracking
        
    Returns:
        Dict containing properly formatted response chunk
    """
    # Get usage information if controller is available
    usage = {}
    if controller and hasattr(controller, 'cost_tracker'):
        usage = {
            "prompt_tokens": controller.cost_tracker.prompt_tokens,
            "completion_tokens": 1,  # Each token is one completion token
            "total_tokens": controller.cost_tracker.prompt_tokens + 1
        }
        # Update controller's completion token count
        controller.cost_tracker.update_completion_tokens(1)
    
    # Use original model name if available
    if controller and hasattr(controller, 'original_model'):
        model = controller.original_model
    
    # Return formatted chunk
    return {
        'id': response_id,
        'object': 'chat.completion.chunk',
        'created': created_time,
        'model': model,
        'choices': [
            {
                'index': 0,
                'delta': {'content': token},
                'finish_reason': None
            }
        ],
        'usage': usage
    }

async def create_stream_response(app, response: Union[Dict[str, Any], AsyncGenerator], controller=None, completion_tokens: int = 0) -> AsyncGenerator:
    """Create a streaming response in the OpenAI format."""
    logging.debug("Processing stream response")
    
    response_id = f"chatcmpl-{shortuuid.random()}"
    created_time = int(time.time())
    model = response.get('model', 'unknown') if isinstance(response, dict) else getattr(response, 'model', 'unknown')
    
    # Get the original model name if available
    if hasattr(controller, 'original_model'):
        model = controller.original_model
    
    # Initialize usage information - only prompt tokens at start
    initial_usage = {
        "prompt_tokens": controller.cost_tracker.prompt_tokens if hasattr(controller, 'cost_tracker') else 0
    }
    
    # Initialize word counter for streamed content
    word_buffer = ""
    word_count = 0
    
    def count_words(text: str) -> int:
        """Count words in text after stripping HTML tags."""
        # Remove HTML tags using regex
        text_without_html = re.sub(r'<[^>]+>', '', text)
        # Split on whitespace and filter out empty strings
        words = [word for word in text_without_html.split() if word.strip()]
        return len(words)
    
    # First chunk with role and initial prompt token usage - only once per stream
    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}], 'usage': initial_usage})}\n\n"
    
    # Content chunks - only include atomic completion tokens
    if isinstance(response, dict):
        content = response.get('content', '')
        if content:
            # Count words in content
            word_count = count_words(content)
            # Use provided completion tokens
            if completion_tokens > 0:
                usage = {
                    "completion_tokens": completion_tokens,
                    "word_count": word_count
                }
                yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}], 'usage': usage})}\n\n"
    elif isinstance(response, (CustomStreamWrapper, AsyncGenerator)):
        try:
            async for chunk in response:
                if chunk is None:
                    continue
                
                # Extract content and update token count
                content = None
                if isinstance(chunk, str):
                    content = chunk
                elif hasattr(chunk, 'choices') and chunk.choices and hasattr(chunk.choices[0], 'delta'):
                    content = chunk.choices[0].delta.content if hasattr(chunk.choices[0].delta, 'content') else None
                
                if content:
                    # Accumulate content for word counting
                    word_buffer += content
                    
                    # Use provided completion tokens
                    if completion_tokens > 0:
                        # Update controller's total count
                        if hasattr(controller, 'cost_tracker'):
                            controller.cost_tracker.update_completion_tokens(completion_tokens)
                        
                        # Report only the new tokens for this chunk
                        usage = {
                            "completion_tokens": completion_tokens,
                            "word_count": count_words(word_buffer)  # Count total words so far
                        }
                        
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}], 'usage': usage})}\n\n"
                
                # If this is a cost disclosure chunk, include both prompt and completion tokens
                if isinstance(chunk, dict) and chunk.get('method') == 'agent/cost_disclosure':
                    disclosure = create_cost_disclosure_dict(
                        prompt_tokens=initial_usage['prompt_tokens'],
                        completion_tokens=controller.cost_tracker.completion_tokens if hasattr(controller, 'cost_tracker') else 0,
                        description=chunk['params']['description']
                    )
                    yield f"data: {json.dumps(disclosure)}\n\n"
                    
        except Exception as e:
            logging.error(f"Error in stream processing: {str(e)}", exc_info=True)
            raise
    
    # Count any remaining words in buffer
    if word_buffer:
        word_count = count_words(word_buffer)
    
    # Update database with word count if we have a controller with user info
    if hasattr(controller, 'user') and word_count > 0:
        try:
            app.cache.update_usage_with_response(
                account_id=int(controller.user),
                prompt_tokens=initial_usage['prompt_tokens'],
                completion_tokens=controller.cost_tracker.completion_tokens if hasattr(controller, 'cost_tracker') else 0,
                word_count=word_count
            )
        except Exception as e:
            logging.error(f"Error updating word count in database: {str(e)}", exc_info=True)
    
    # Final chunk with finish reason - only once per stream
    final_usage = {
        'completion_tokens': controller.cost_tracker.completion_tokens if hasattr(controller, 'cost_tracker') else 0,
        'word_count': word_count
    }
    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': final_usage})}\n\n"
    yield "data: [DONE]\n\n"

def predefined_completion_response(base_response, controller=None, **kwargs):
    """
    Construct a ChatCompletionResponse from a base response (dict) and various optional keyword arguments.
    
    Args:
        base_response (dict): a base response (dict) returned from the chat completion API
        controller: Optional controller instance that may contain the original model name
        id (str, optional): id of the response. Defaults to None.
        object (str, optional): object of the response. Defaults to "chat.completion".
        created (int, optional): created time of the response. Defaults to int(time.time()).
        model (str, optional): model used for the response. Defaults to 'predefined_prompt'.
        choices (List[ChatCompletionResponseChoice], optional): choices of the response.
        usage (UsageInfo, optional): usage of the response.
    
    Returns:
        ChatCompletionResponse: a constructed ChatCompletionResponse
    """
    # initialize response
    id = kwargs.get('id', f"chatcmpl-{shortuuid.random()}")
    object = kwargs.get('object', "chat.completion")
    created = kwargs.get('created', int(time.time()))
    
    # Get model name, preferring original model from controller if available
    if controller and hasattr(controller, 'original_model'):
        model = controller.original_model
    else:
        model = kwargs.get('model', 'predefined_prompt')
    
    choices = kwargs.get('choices', [ChatCompletionResponseChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=base_response['choices'][0]['message']['content']),
                    finish_reason="stop"
                )])
    usage = kwargs.get('usage', UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0))
    
    # initialize response
    return ChatCompletionResponse(
                id=id,
                object=object,
                created=created,
                model=model,
                choices=choices,
                usage=usage
            )

class ErrorResponse(BaseModel):
    """Error response from the API."""
    object: Literal["error"] = Field(
        "error",
        description="Always 'error' for error responses"
    )
    message: str = Field(
        ...,
        description="Error message details"
    )


class UsageInfo(BaseModel):
    """Token usage information for API calls."""
    prompt_tokens: int = Field(
        default=0,
        # ge=0,
        description="Number of tokens in the prompt"
    )
    total_tokens: int = Field(
        default=0,
        # ge=0,
        description="Total tokens used in the request"
    )
    completion_tokens: Optional[int] = Field(
        None,
        # ge=0,
        description="Number of tokens in the completion"
    )


class ChatMessage(BaseModel):
    """A single message in a chat conversation."""
    role: Literal["system", "user", "assistant"] = Field(
        ...,
        description="Role of the message sender"
    )
    content: str = Field(
        ...,
        description="Content of the message"
    )


class ChatCompletionResponseChoice(BaseModel):
    """A single completion choice in a chat response."""
    index: int = Field(
        ...,
        # ge=0,
        description="Index of this choice in the list of choices"
    )
    message: ChatMessage = Field(
        ...,
        description="The message containing the completion"
    )
    finish_reason: Optional[Literal["stop", "length"]] = Field(
        None,
        description="Why the completion stopped"
    )


class ChatCompletionResponse(BaseModel):
    """Response from a chat completion request."""
    id: str = Field(
        default_factory=lambda: f"chatcmpl-{shortuuid.random()}",
        description="Unique identifier for this completion"
    )
    object: Literal["chat.completion"] = Field(
        "chat.completion",
        description="Type of object returned"
    )
    created: int = Field(
        default_factory=lambda: int(time.time()),
        description="Unix timestamp of when this completion was created"
    )
    model: str = Field(
        ...,
        description="Model used for the completion"
    )
    original_model: str = Field(
        ...,
        description="Model asked for by the request"
    )
    choices: List[ChatCompletionResponseChoice] = Field(
        ...,
        min_items=1,
        description="List of completion choices"
    )
    usage: UsageInfo = Field(
        default_factory=UsageInfo,
        description="Token usage information"
    )
    total_spent_tokens: Optional[Dict[str, float]] = Field(
        None,
        description="Total cumulative tokens spent by the user"
    )
    total_spent_input_tokens: Optional[float] = Field(
        None,
        description="Total cumulative input tokens spent by the user"
    )
    total_spent_output_tokens: Optional[float] = Field(
        None,
        description="Total cumulative output tokens spent by the user"
    )


class ChatCompletionRequest(BaseModel):
    """Request for a chat completion."""
    model: str = Field(
        ...,
        description="ID of the model to use"
    )
    original_model: Optional[str] = Field(
        None,
        description="Original model name before translation"
    )
    messages: Union[
        str,
        List[Dict[str, str]],
        List[Dict[str, Union[str, List[Dict[str, Union[str, Dict[str, str]]]]]]],
    ] = Field(
        ...,
        description="Messages to generate chat completions for"
    )
    frequency_penalty: Optional[float] = Field(
        default=0.0,
        # ge=-2.0,
        # le=2.0,
        description="Penalty for token frequency"
    )
    logit_bias: Optional[Dict[int, float]] = Field(
        None,
        description="Modify likelihood of specific tokens"
    )
    logprobs: Optional[bool] = Field(
        None,
        description="Include log probabilities in response"
    )
    top_logprobs: Optional[int] = Field(
        None,
        # ge=0,
        description="Number of most likely tokens to return"
    )
    max_tokens: Optional[int] = Field(
        None,
        description="Maximum number of tokens to generate"
    )
    n: Optional[int] = Field(
        default=1,
        description="Number of chat completion choices to generate"
    )
    config: Optional[Dict[str, Any]] = Field(
        None,
        description="""Additional configuration for model behavior. Supports:
        - response_mime_type: str - The MIME type of the response ('application/json' or 'text/x.enum')
        - response_schema: Union[Type, Dict[str, Any]] - Schema definition for structured output
            Can be:
            - A type annotation (e.g., list[Recipe])
            - A dict representing an OpenAPI 3.0 schema
            - An enum class for constrained choices"""
    )
    seed: Optional[int] = Field(
        None,
        description="Random seed for deterministic results"
    )
    stop: Optional[Union[str, List[str]]] = Field(
        None,
        description="Sequences where the API will stop generating"
    )
    stream: Optional[bool] = Field(
        default=False,
        description="Whether to stream partial progress"
    )
    temperature: Optional[float] = Field(
        default=1.0,
        # ge=0.0,
        # le=2.0,
        description="Sampling temperature"
    )
    top_p: Optional[float] = Field(
        default=1.0,
        # ge=0.0,
        # le=1.0,
        description="Nucleus sampling parameter"
    )
    tools: Optional[List[Dict[str, Union[str, int, float]]]] = Field(
        None,
        description="List of tools the model may call"
    )
    tool_choice: Optional[str] = Field(
        None,
        description="Control which tool is used"
    )
    user: Optional[str] = Field(
        None,
        description="Unique identifier for the end-user"
    )
    allowed_html_tags: Optional[str] = Field(
        None,
        description="Comma-separated list of allowed HTML tags"
    )
    allowed_html_classes: Optional[str] = Field(
        None,
        description="Comma-separated list of allowed HTML classes"
    )
    router_usage: Optional[Dict[str, int]] = Field(
        None,
        description="Token usage information from the router analysis"
    )


# ######################
# ROUTING MODELS
# ######################
class RoutingAnalysis(BaseModel):
    """Schema for the routing analysis response.
    Used to determine if content should be handled by Longwriter.
    """
    length_score: float = Field(
        ...,
        description="Probability (0.0-1.0) that response will be >1000 words",
        # ge=0.0,
        # le=1.0
    )
    needs_structure: bool = Field(
        ...,
        description="If content needs strategic planning and organization"
    )
    is_data_dump: bool = Field(
        ...,
        description="If it's primarily a list/data without narrative"
    )

class WebSearchResult(BaseModel):
    """Search result from web."""
    title: str
    url: str
    summary: str

class WebSearchEvaluation(BaseModel):
    """Model for evaluating web search necessity. Higher score (0-100) indicates greater need for web search.
    Examples: mathematical facts score low (5), current events score high (85)."""
    score: int = Field(..., ge=0, le=100)
    search_required: bool = Field(default=None)  # Will be set based on score and threshold

    def compute_search_required(self, threshold: int) -> None:
        """Set search_required based on score and threshold comparison."""
        self.search_required = self.score > threshold

# ######################
# LONGWRITER MODELS
# ######################
class ContentRequest(BaseModel):
    """Request model for content generation."""
    prompt: str = Field(
        ...,
        description="The main content prompt to generate content for"
    )
    allowed_html_tags: str = Field(
        ...,
        description="Comma-separated list of allowed HTML tags"
    )
    allowed_html_classes: Optional[str] = Field(
        None,
        description="Comma-separated list of allowed HTML classes"
    )
    messages: Optional[List[Dict[str, str]]] = Field(
        None,
        description="Optional chat messages for context"
    )
    user: Optional[str] = Field(
        None,
        description="User ID for token tracking"
    )

class ContentStrategy(BaseModel):
    """Strategic plan for content generation based on E-E-A-T framework."""
    strategy: str = Field(
        ...,
        description="Overall content strategy and approach"
    )
    content_scope: str = Field(
        ...,
        description="Defined scope and boundaries of the content"
    )
    recommended_word_count: int = Field(
        ...,
        description="Recommended total word count for the content"
    )
    key_questions: List[str] = Field(
        ...,
        description="Key questions the content should answer"
    )
    original_messages: Optional[List[str]] = Field(
        default=None,
        description="Original chat messages for context preservation"
    )
    user: Optional[str] = Field(
        None,
        description="User ID for token tracking"
    )

class HTMLTagStrategy(BaseModel):
    """Strategy for HTML tag usage in content."""
    tags: List[str] = Field(
        ...,
        description="List of HTML tags to use in content formatting"
    )
    classes: Optional[List[str]] = Field(
        default_factory=list,
        description="List of HTML classes to use in content formatting"
    )

class OutlineSection(BaseModel):
    """Section in the content outline."""
    title: str = Field(
        ...,
        description="Section title"
    )
    description: str = Field(
        ...,
        description="Detailed description of section content"
    )
    content_ideas: List[str] = Field(
        ...,
        description="List of content ideas and key points for the section"
    )
    multimedia_notes: str = Field(
        ...,
        description="Notes about multimedia elements to include"
    )
    target_word_count: int = Field(
        ...,
        description="Target word count for this section"
    )

class ContentOutline(BaseModel):
    """Complete content outline with sections."""
    sections: List[OutlineSection] = Field(
        ...,
        description="List of content sections"
    )
    total_word_count: int = Field(
        description="Total word count across all sections"
    )

    @field_validator('total_word_count', mode='before')
    @classmethod
    def set_total_word_count(cls, v, info):
        """Calculate total word count from sections if not provided."""
        if v == 0 and 'sections' in info.data:
            total = sum(section.target_word_count for section in info.data['sections'])
            if total > 10000:  # Enforce maximum even in calculated total
                raise ValueError("Total word count exceeds maximum limit of 10000")
            return total
        return v

    @field_validator('sections')
    @classmethod
    def validate_section_totals(cls, v):
        """Validate that section word counts don't exceed total limit."""
        total = sum(section.target_word_count for section in v)
        if total > 10000:
            raise ValueError("Combined section word counts exceed maximum limit of 10000")
        return v

class ContentDraft(BaseModel):
    """Draft content for a section."""
    content: str = Field(
        ...,
        description="The actual content draft"
    )

class FullContent(BaseModel):
    """Complete generated content."""
    content: str = Field(
        ...,
        description="The complete generated content"
    )
    model: str = Field(
        ...,
        description="The model used to generate the content"
    )

class EnumResponse(BaseModel):
    """Response containing a constrained choice from an enum."""
    value: str = Field(
        ...,
        description="The selected enum value"
    )
    enum_type: str = Field(
        ...,
        description="The name of the enum type"
    )
    possible_values: List[str] = Field(
        ...,
        description="List of all possible enum values"
    )

    @classmethod
    def from_enum(cls, enum_class: type, selected_value: str) -> 'EnumResponse':
        """Create an EnumResponse from an enum class and selected value."""
        return cls(
            value=selected_value,
            enum_type=enum_class.__name__,
            possible_values=[e.value for e in enum_class]
        )

# Token Tracking Models
class TokenAmount(BaseModel):
    """Base model for token amounts with validation."""
    amount: float = Field(
        ...,
        ge=0.0,
        description="Number of tokens"
    )
    
    @field_validator('amount')
    def validate_non_negative(cls, v: float) -> float:
        """Ensure token amounts are non-negative."""
        if v < 0:
            raise ValueError("Token amounts must be non-negative")
        return v

class AccountTokenBalance(BaseModel):
    """Current token and word balances for an account."""
    account_id: int = Field(
        ..., 
        gt=0,
        description="Unique identifier for the account"
    )
    token_balance_in: float = Field(
        ...,
        ge=0.0,
        description="Available input tokens"
    )
    token_balance_out: float = Field(
        ...,
        ge=0.0,
        description="Available output tokens"
    )
    word_balance: int = Field(
        ...,
        ge=0,
        description="Available word count balance"
    )
    total_token_usage_in: float = Field(
        ...,
        ge=0.0,
        description="Total input tokens used historically"
    )
    total_token_usage_out: float = Field(
        ...,
        ge=0.0,
        description="Total output tokens used historically"
    )
    total_word_usage: int = Field(
        ...,
        ge=0,
        description="Total words streamed historically"
    )
    transactions: int = Field(
        ...,
        ge=0,
        description="Total number of transactions"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "account_id": 1,
                    "token_balance_in": 3000000.0,
                    "token_balance_out": 1000000.0,
                    "word_balance": 10000,
                    "total_token_usage_in": 50000.0,
                    "total_token_usage_out": 10000.0,
                    "total_word_usage": 2500,
                    "transactions": 42
                }
            ]
        }
    }

class DailyUsageSummary(BaseModel):
    """Daily token and word usage summary for an account."""
    account_id: int = Field(
        ...,
        gt=0,
        description="Account identifier"
    )
    date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Usage date in YYYY-MM-DD format"
    )
    transaction_count: int = Field(
        ...,
        ge=0,
        description="Number of transactions on this date"
    )
    daily_token_usage_in: float = Field(
        ...,
        ge=0.0,
        description="Input tokens used on this date"
    )
    daily_token_usage_out: float = Field(
        ...,
        ge=0.0,
        description="Output tokens used on this date"
    )
    daily_word_usage: int = Field(
        ...,
        ge=0,
        description="Number of words streamed on this date"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "account_id": 1,
                    "date": "2024-02-20",
                    "transaction_count": 5,
                    "daily_token_usage_in": 1500.0,
                    "daily_token_usage_out": 300.0,
                    "daily_word_usage": 250
                }
            ]
        }
    }

class TokenUsageUpdate(BaseModel):
    """Token and word usage update request."""
    account_id: int = Field(
        ...,
        gt=0,
        description="Account to update"
    )
    prompt_tokens: int = Field(
        ...,
        ge=0,
        description="Number of prompt tokens to deduct"
    )
    completion_tokens: int = Field(
        ...,
        ge=0,
        description="Number of completion tokens to deduct"
    )
    word_count: int = Field(
        default=0,
        ge=0,
        description="Number of words to deduct from word balance"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "account_id": 1,
                    "prompt_tokens": 150,
                    "completion_tokens": 50,
                    "word_count": 25
                }
            ]
        }
    }

class TokenError(BaseModel):
    """Base class for token-related errors."""
    code: str = Field(
        ...,
        description="Error code"
    )
    message: str = Field(
        ...,
        description="Error message"
    )
    type: Literal["token_error"] = Field(
        "token_error",
        description="Type of error"
    )

class InsufficientTokensError(TokenError):
    """Error response for insufficient token balance."""
    code: Literal["insufficient_tokens"] = Field(
        "insufficient_tokens",
        description="Error code for insufficient tokens"
    )
    current_balance: AccountTokenBalance = Field(
        ...,
        description="Current account balance"
    )
    required_tokens: TokenUsageUpdate = Field(
        ...,
        description="Required tokens for operation"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "code": "insufficient_tokens",
                    "message": "Insufficient token balance",
                    "type": "token_error",
                    "current_balance": {
                        "account_id": 1,
                        "token_balance_in": 100.0,
                        "token_balance_out": 50.0,
                        "transactions": 10
                    },
                    "required_tokens": {
                        "account_id": 1,
                        "prompt_tokens": 150,
                        "completion_tokens": 75
                    }
                }
            ]
        }
    }

class TokenUsageResponse(BaseModel):
    """Response for a successful token usage update."""
    account_id: int = Field(
        ...,
        gt=0,
        description="Account identifier"
    )
    new_balance: AccountTokenBalance = Field(
        ...,
        description="Updated account balance"
    )
    usage: TokenUsageUpdate = Field(
        ...,
        description="Token usage details"
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="Timestamp of the update"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "account_id": 1,
                    "new_balance": {
                        "account_id": 1,
                        "token_balance_in": 2999850.0,
                        "token_balance_out": 999950.0,
                        "transactions": 11
                    },
                    "usage": {
                        "account_id": 1,
                        "prompt_tokens": 150,
                        "completion_tokens": 50
                    },
                    "timestamp": "2024-02-20T15:30:45.123456"
                }
            ]
        }
    }

class KavyaRequest(BaseModel):
    """Request model for Kavya API validation."""
    model: Literal["kavya-m1", "kavya-m1-eu", "kavya-m1-hyper"] = Field(
        ...,
        description="The Kavya model to use"
    )
    messages: List[ChatMessage] = Field(
        ...,
        description="The messages to process",
        min_items=1
    )
    stream: Optional[bool] = Field(
        default=False,
        description="Whether to stream the response"
    )
    user: Optional[str] = Field(
        None,
        description="A unique identifier for the user"
    )
    allowed_html_tags: Optional[str] = Field(
        None,
        description="Comma-separated list of allowed HTML tags"
    )
    allowed_html_classes: Optional[str] = Field(
        None,
        description="Comma-separated list of allowed HTML classes"
    )
    providers: Optional[str] = Field(
        None,
        description="Comma-separated list of provider names to use as fallbacks (only valid with kavya-m1 model)"
    )