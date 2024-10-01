"""A server that provides OpenAI-compatible RESTful APIs.

It current only supports Chat Completions: https://platform.openai.com/docs/api-reference/chat)
"""

import argparse
import logging
import os
import time
import sys
from collections import defaultdict
from typing import AsyncGenerator, Dict, List, Literal, Optional, Union, Any
import json

import fastapi
import shortuuid
import uvicorn
import yaml
from fastapi.concurrency import asynccontextmanager
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from routellm.controller import Controller, RoutingError
from routellm.routers.routers import ROUTER_CLS

from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Load environment variables from .env file
load_dotenv()

os.environ["TOKENIZERS_PARALLELISM"] = "false"
CONTROLLER = None

openai_client = AsyncOpenAI()
count = defaultdict(lambda: defaultdict(int))


@asynccontextmanager
async def lifespan(app):
    global CONTROLLER
    logging.debug("Initializing CONTROLLER")
    try:
        CONTROLLER = Controller(
            routers=args.routers,
            config=yaml.safe_load(open(args.config, "r")) if args.config else None,
            strong_model=args.strong_model,
            weak_model=args.weak_model,
            api_base=args.base_url,
            api_key=args.api_key,
            progress_bar=True,
        )
        logging.debug("CONTROLLER initialized successfully")
    except Exception as e:
        logging.error(f"Failed to initialize CONTROLLER: {str(e)}")
    yield
    CONTROLLER = None
    logging.debug("CONTROLLER shut down")


app = fastapi.FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.post("/v1/chat/completions")
async def create_chat_completion(request: ChatCompletionRequest):
    logging.info(f"Received request: {request}")
    try:
        res = await CONTROLLER.acompletion(
            **request.model_dump(exclude_none=True),
        )
        is_predefined = isinstance(res, dict) and res.get('model') == 'predefined_prompt'
        chosen_model = res['model'] if is_predefined else res.model
    except RoutingError as e:
        return JSONResponse(
            ErrorResponse(message=str(e)).model_dump(),
            status_code=400,
        )

    logging.info(CONTROLLER.model_counts)

    if request.stream:
        return StreamingResponse(
            content=stream_response(res),
            media_type="text/event-stream",
            headers={"X-Chosen-Model": chosen_model}
        )
    else:
        if is_predefined:
            content = ChatCompletionResponse(
                id=f"chatcmpl-{shortuuid.random()}",
                object="chat.completion",
                created=int(time.time()),
                model="predefined_prompt",
                choices=[ChatCompletionResponseChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=res['choices'][0]['message']['content']),
                    finish_reason="stop"
                )],
                usage=UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0)
            ).model_dump()
        else:
            content = res.model_dump()
        return JSONResponse(content=content, headers={"X-Chosen-Model": chosen_model})

@app.get("/", response_class=HTMLResponse)
@app.get("/health", response_class=HTMLResponse)
async def health_check():
    """Health check endpoint."""
    logging.debug("Health check called")
    html_content = """
    <!DOCTYPE html>
    <html>
        <head>
            <title>RouteLLM Server Status</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding-top: 50px; }
                h1 { color: #4CAF50; }
            </style>
        </head>
        <body>
            <h1>RouteLLM Server Status</h1>
            <p>Status: <strong>Online</strong></p>
            <p>Server is running and ready to handle requests.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


parser = argparse.ArgumentParser(
    description="An OpenAI-compatible API server for LLM routing."
)
parser.add_argument(
    "--verbose",
    action="store_true",
)
parser.add_argument("--workers", type=int, default=0)
parser.add_argument("--config", type=str, default=None)
parser.add_argument("--port", type=int, default=6060)
parser.add_argument(
    "--routers",
    nargs="+",
    type=str,
    default=["random"],
    choices=list(ROUTER_CLS.keys()),
)
parser.add_argument(
    "--base-url",
    help="The base URL used for all LLM requests",
    type=str,
    default=None,
)
parser.add_argument(
    "--api-key",
    help="The API key used for all LLM requests",
    type=str,
    default=None,
)
parser.add_argument("--strong-model", type=str, default="gpt-4-1106-preview")
parser.add_argument(
    "--weak-model", type=str, default="anyscale/mistralai/Mixtral-8x7B-Instruct-v0.1"
)
args = parser.parse_args()

if args.verbose:
    logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    print("Launching server with routers:", args.routers)
    uvicorn.run(
        "routellm.openai_server:app",
        port=args.port,
        host="0.0.0.0",
        workers=args.workers,
    )