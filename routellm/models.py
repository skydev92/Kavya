import time
import json
import shortuuid
import logging
import sys

from pydantic import BaseModel, Field, field_validator
from typing import AsyncGenerator, Dict, List, Literal, Optional, Union, Any
from litellm import CustomStreamWrapper

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
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

async def create_stream_response(response: Union[Dict[str, Any], AsyncGenerator]) -> AsyncGenerator:
    logging.debug("create_stream_response is triggered")
    if isinstance(response, dict):
        logging.debug("create_stream_response : response is a dict")
        # normal response
        if "choices" in response:
            if "message" in response['choices'][0]:
                content = response['choices'][0]['message']['content']
            else:
                content = response['choices'][0]['delta']['content']
        # minimalistic response
        else:
            content = response['content']
        
        if "model" in response:
            model = response['model']
        else:
            model = "predefined_prompt"
        
        response_id = f"chatcmpl-{shortuuid.random()}"
        created_time = int(time.time())

        # Handle structured output in the response
        if isinstance(content, (dict, list)):
            content = json.dumps(content)

        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model' : model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
        
        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model' : model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"

        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model' : model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
    elif isinstance(response, CustomStreamWrapper):
        logging.debug("create_stream_response : response is a CustomStreamWrapper")

        model = response.model
        response_id = f"chatcmpl-{shortuuid.random()}"
        created_time = int(time.time())

        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model' : model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"

        try:
            while True:
                chunk = next(response)
                # if there's ever an error after an update, it likely comes from the line below
                dict_chunk = chunk.json() # actually converts to dict because of older pydantic version

                logging.debug("strange json : "+ json.dumps(json.loads(json.dumps(dict_chunk))))
                content = dict_chunk['choices'][0]['delta']['content']
                if content is not None:
                    # Handle structured output in streaming chunks
                    if isinstance(content, (dict, list)):
                        content = json.dumps(content)
                    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model' : model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
        except StopIteration:
            pass

        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model' : model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
    else:
        logging.debug("create_stream_response : response is not a dict")
        async for chunk in response:
            yield f"data: {chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"

def predefined_completion_response(base_response, **kwargs):
    """
    Construct a ChatCompletionResponse from a base response (dict) and various optional keyword arguments.
    
    Args:
        base_response (dict): a base response (dict) returned from the chat completion API
        id (str, optional): id of the response. Defaults to None.
        object (str, optional): object of the response. Defaults to "chat.completion".
        created (int, optional): created time of the response. Defaults to int(time.time()).
        model (str, optional): model used for the response. Defaults to 'predefined_prompt'.
        choices (List[ChatCompletionResponseChoice], optional): choices of the response. Defaults to [ChatCompletionResponseChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=base_response['choices'][0]['message']['content']),
                    finish_reason="stop"
                )].
        usage (UsageInfo, optional): usage of the response. Defaults to UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0).
    
    Returns:
        ChatCompletionResponse: a constructed ChatCompletionResponse
    """
    # initialize response
    id = kwargs.get('id', f"chatcmpl-{shortuuid.random()}")
    object = kwargs.get('object', "chat.completion")
    created = kwargs.get('created', int(time.time()))
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
            ).model_dump()

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
        ge=0,
        description="Number of tokens in the prompt"
    )
    total_tokens: int = Field(
        default=0,
        ge=0,
        description="Total tokens used in the request"
    )
    completion_tokens: Optional[int] = Field(
        None,
        ge=0,
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
        ge=0,
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
    choices: List[ChatCompletionResponseChoice] = Field(
        ...,
        min_items=1,
        description="List of completion choices"
    )
    usage: UsageInfo = Field(
        default_factory=UsageInfo,
        description="Token usage information"
    )


class ChatCompletionRequest(BaseModel):
    """Request for a chat completion."""
    model: str = Field(
        ...,
        description="ID of the model to use"
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
        ge=-2.0,
        le=2.0,
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
        ge=0,
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
        ge=0.0,
        le=2.0,
        description="Sampling temperature"
    )
    top_p: Optional[float] = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
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


# ######################
# ROUTING MODELS
# ######################
class RoutingAnalysis(BaseModel):
    """Schema for the routing analysis response.
    Used to determine if content should be handled by Longwriter.
    """
    length_score: float = Field(
        ...,
        description="Probability (0.0-1.0) that response will be >700 words",
        ge=0.0,
        le=1.0
    )
    needs_structure: bool = Field(
        ...,
        description="If content needs strategic planning and organization"
    )
    is_data_dump: bool = Field(
        ...,
        description="If it's primarily a list/data without narrative"
    )

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
    messages: Optional[List[Dict[str, str]]] = Field(
        None,
        description="Optional chat messages for context"
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
        ge=1,
        le=10000,
        description="Recommended total word count for the content"
    )
    key_questions: List[str] = Field(
        ...,
        min_items=1,
        description="Key questions the content should answer"
    )
    original_messages: Optional[List[str]] = Field(
        default=None,
        description="Original chat messages for context preservation"
    )

class HTMLTagStrategy(BaseModel):
    """Strategy for HTML tag usage in content."""
    tags: List[str] = Field(
        ...,
        min_items=1,
        description="List of HTML tags to use in content formatting"
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
        min_items=1,
        description="List of content ideas and key points for the section"
    )
    multimedia_notes: str = Field(
        ...,
        description="Notes about multimedia elements to include"
    )
    target_word_count: int = Field(
        ...,
        le=5000,
        description="Target word count for this section"
    )

class ContentOutline(BaseModel):
    """Complete content outline with sections."""
    sections: List[OutlineSection] = Field(
        ...,
        min_items=1,
        description="List of content sections"
    )
    total_word_count: int = Field(
        ge=0,
        le=10000,
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