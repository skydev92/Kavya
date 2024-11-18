"""A server that provides OpenAI-compatible RESTful APIs.

It current only supports Chat Completions: https://platform.openai.com/docs/api-reference/chat)
"""

import argparse
import os
import sys
import asyncio
import yaml
import json

import logging
import fastapi
import uvicorn

from fastapi.concurrency import asynccontextmanager
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from routellm.controller import Controllers, RoutingError
from routellm.routers.routers import ROUTER_CLS
import routellm.models 

from dotenv import load_dotenv

# ------------------------------------------------------------------------------
# APPLICATION INITIALIZATION
# ------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_):
    logging.debug("Initializing controllers")
    # try:
    app.controllers = Controllers(
        routers=args.routers,
        config=yaml.safe_load(open(args.config, "r")) if args.config else None,
        strong_model=args.strong_model,
        weak_model=args.weak_model,
        api_base=args.base_url,
        api_key=args.api_key,
        progress_bar=True,
    )
    app.controllers.create_controller("completion", 
        routers=args.routers,
        config=yaml.safe_load(open(args.config, "r")) if args.config else None,
        strong_model=args.strong_model,
        weak_model=args.weak_model,
        api_base=args.base_url,
        api_key=args.api_key,
        progress_bar=True,
    )
    # create as many controllers as needed, below :
    # app.controllers.create_controller("title_generator", **vars(args))
    # app.controllers.create_controller("paraphrase", **vars(args))
    logging.debug("Default controller based on arguments, initialized successfully")
    # except Exception as e:
        # logging.error(f"Failed to initialize controllers: {str(e)}")
    
    yield

    app.controllers = []
    logging.debug("All controllers shut down")

app = fastapi.FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------------------
# UTILITY ENDPOINTS
# ------------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
@app.get("/health", response_class=HTMLResponse)
async def health_check():
    """Health check endpoint."""
    logging.debug("Health check called")
    html_content = """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Kavya AI Server Status</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    text-align: center;
                    padding-top: 50px;
                    margin: 0;
                    height: 100vh;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    align-items: center;
                    background-image: url('https://i.giphy.com/b421Wq4bQ9tMGEPmTN.webp');
                    background-repeat: no-repeat;
                    background-size: cover;
                    background-position: center;
                }
                .content {
                    background-color: rgba(255, 255, 255, 0.8);
                    padding: 20px;
                    border-radius: 10px;
                }
                h1 { color: #4CAF50; }
            </style>
        </head>
        <body>
            <div class="content">
                <h1>Kavya AI Server Status</h1>
                <p>Status: <strong>Online</strong></p>
                <p>Server is running and ready to handle requests.</p>
                <p>Copyright 2024 DXPR</p>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

# ------------------------------------------------------------------------------
# API ENDPOINTS
# ------------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def create_chat_completion(request: routellm.models.ChatCompletionRequest):
    logging.info(f"Received request: {request}")
    try:
        res = await app.controllers.response(request, "completion", "acompletion")
        # print(json.dumps(res))
        is_predefined = isinstance(res, dict) and res.get('model') == 'predefined_prompt'
        chosen_model = res['model'] if is_predefined else res.model
    except RoutingError as e:
        return JSONResponse(
            routellm.models.ErrorResponse(message=str(e)).model_dump(),
            status_code=400,
        )

    logging.info(app.controllers.completion.model_counts)

    if request.stream:
        return StreamingResponse(
            content=routellm.models.stream_response(res),
            media_type="text/event-stream",
            headers={"X-Chosen-Model": chosen_model}
        )
    else:
        if is_predefined:
            content = routellm.models.predefined_completion_response(res).model_dump()
        else:
            content = res.model_dump()
        return JSONResponse(content=content, headers={"X-Chosen-Model": chosen_model})

# ------------------------------------------------------------------------------
# MAIN : APPLICATION STARTUP
# ------------------------------------------------------------------------------

# if __name__ == "__main__":
# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Load environment variables from .env file
load_dotenv()

os.environ["TOKENIZERS_PARALLELISM"] = "false"

parser = argparse.ArgumentParser(
    description="An OpenAI-compatible API server for LLM routing."
)
parser.add_argument(
    "--verbose",
    action="store_true",
)
parser.add_argument("--workers", type=int, default=0)
parser.add_argument("--config", type=str, default=None)
parser.add_argument("--port", type=int, default=8080)
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

if not asyncio.get_event_loop().is_running():
    print("Launching server with routers:", args.routers)
    uvicorn.run(
        "routellm.openai_server:app",
        port=8080,
        host="0.0.0.0",
        workers=args.workers,
    )