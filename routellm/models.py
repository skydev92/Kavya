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
    # level=logging.DEBUG,
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
    object: str = "error"
    message: str


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0
    completion_tokens: Optional[int] = 0


class ChatCompletionRequest(BaseModel):
    # OpenAI fields: https://platform.openai.com/docs/api-reference/chat/create
    model: str
    messages: Union[
        str,
        List[Dict[str, str]],
        List[Dict[str, Union[str, List[Dict[str, Union[str, Dict[str, str]]]]]]],
    ]
    frequency_penalty: Optional[float] = 0.0
    logit_bias: Optional[Dict[int, float]] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    max_tokens: Optional[int] = None
    n: Optional[int] = 1
    presence_penalty: Optional[float] = 0.0
    response_format: Optional[Dict[str, str]] = (
        None  # { "type": "json_object" } for json mode
    )
    seed: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    tools: Optional[List[Dict[str, Union[str, int, float]]]] = None
    tool_choice: Optional[str] = None
    user: Optional[str] = None
    allowed_html_tags: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[Literal["stop", "length"]] = None


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{shortuuid.random()}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChatCompletionResponseChoice]
    usage: UsageInfo










# ######################
# LONGWRITER MODELS
# ######################
class ContentRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    allowed_html_tags: str = Field(..., min_length=1)
    messages: Optional[List[Dict[str, str]]] = None

class ContentStrategy(BaseModel):
    strategy: str
    content_scope: str
    recommended_word_count: int
    key_questions: List[str]
    original_messages: Optional[List[Dict[str, str]]] = None

class HTMLTagStrategy(BaseModel):
    tags: List[str]

class OutlineSection(BaseModel):
    title: str
    description: str
    content_ideas: List[str]
    multimedia_notes: str
    target_word_count: int

class ContentOutline(BaseModel):
    sections: List[OutlineSection]
    total_word_count: int = Field(default=0)

    @field_validator('total_word_count', mode='before')
    @classmethod
    def set_total_word_count(cls, v, info):
        if v == 0 and 'sections' in info.data:
            return sum(section.target_word_count for section in info.data['sections'])
        return v

class ContentDraft(BaseModel):
    content: str

class FullContent(BaseModel):
    content: str
    model: str