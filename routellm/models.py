import time
import json
import shortuuid


from pydantic import BaseModel, Field
from typing import AsyncGenerator, Dict, List, Literal, Optional, Union, Any

async def stream_response(response: Union[Dict[str, Any], AsyncGenerator]) -> AsyncGenerator:
    if isinstance(response, dict):
        content = response['choices'][0]['message']['content']
        response_id = f"chatcmpl-{shortuuid.random()}"
        created_time = int(time.time())

        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': 'predefined_prompt', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"

        for char in content:
            yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': 'predefined_prompt', 'choices': [{'index': 0, 'delta': {'content': char}, 'finish_reason': None}]})}\n\n"

        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': 'predefined_prompt', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
    else:
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