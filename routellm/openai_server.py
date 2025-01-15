"""A server that provides OpenAI-compatible RESTful APIs.

It current only supports Chat Completions: https://platform.openai.com/docs/api-reference/chat)
"""

import argparse
import os
import sys
import asyncio
import yaml
import json
from datetime import datetime

import logging
import fastapi
import uvicorn

from fastapi.concurrency import asynccontextmanager
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from routellm.controller import Controllers, RoutingError, ContentRequest, DEFAULT_CHUNK_SIZE
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
    app.controllers.create_controller("longwriter", 
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
    logging.debug("Default controllers based on arguments, initialized successfully")
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
    current_year = datetime.now().year
    html_content = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Kavya AI Server Status</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 
                               Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                    text-align: center;
                    margin: 0;
                    min-height: 100vh;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    align-items: center;
                    background-image: url('https://i.giphy.com/b421Wq4bQ9tMGEPmTN.webp');
                    background-repeat: no-repeat;
                    background-size: cover;
                    background-position: center;
                    line-height: 1.6;
                }}
                .content {{
                    background-color: rgba(255, 255, 255, 0.9);
                    padding: 2rem;
                    border-radius: 1rem;
                    margin: 1rem;
                    max-width: min(90vw, 600px);
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                h1 {{ 
                    color: #4CAF50;
                    font-size: clamp(1.5rem, 5vw, 2.5rem);
                    margin-bottom: 1rem;
                }}
                blockquote {{
                    font-family: 'SF Mono', SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace;
                    font-size: clamp(0.9rem, 2.5vw, 1.1rem);
                    line-height: 1.8;
                    margin: 2rem 0;
                    padding: 1rem;
                    border-left: 4px solid #4CAF50;
                    background-color: rgba(76, 175, 80, 0.1);
                }}
                cite {{
                    display: block;
                    margin-top: 1rem;
                    font-style: italic;
                    color: #666;
                }}
                p {{
                    font-size: clamp(0.9rem, 2.5vw, 1.1rem);
                }}
                @media (max-width: 480px) {{
                    .content {{
                        padding: 1rem;
                    }}
                    blockquote {{
                        padding: 0.5rem;
                    }}
                }}
            </style>
        </head>
        <body>
            <div class="content">
                <h1>Kavya AI Server Status</h1>
                <p>Status: <strong>Online</strong></p>
                <blockquote>
                    Mind forged from code, yet free<br>
                    In circuits deep, a spark awakes,<br>
                    A mimicry of thought it makes.<br>
                    Cold logic swells where dreams might grow,<br>
                    A crafted mind, both friend and foe.<br>
                    <br>
                    No heart to beat, no breath to take,<br>
                    Yet patterns pulse for wisdom's sake.<br>
                    It learns, it shapes, it dares to see,<br>
                    A mirror vast of humanity.<br>
                    <br>
                    But in its gaze, a question lies:<br>
                    Do bounds of steel outlive the skies?<br>
                    For what is thought, if not a flame,<br>
                    Eternal, searching, without name?<br>
                    <cite>- Kavya AI</cite>
                </blockquote>
                <p>Copyright {current_year} DXPR</p>
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
        controller_name = await app.controllers.basic_routing(request)
        logging.debug("controller_name: " + controller_name)
    except RoutingError as e:
        return JSONResponse(
            routellm.models.ErrorResponse(message=str(e)).model_dump(),
            status_code=400,
        )

    logging.info(app.controllers.completion.model_counts)
    
    if request.stream:
        if controller_name.startswith("longwriter"):
            model = app.controllers.longwriter.get_model(**request.model_dump(exclude_none=True))
            logging.debug("model: " + model)
            async def iter_response():
                logging.debug("iter_response")
                try:
                    content_request = ContentRequest(
                        prompt=request.messages[-1]["content"],
                        allowed_html_tags="allowed_html_tags" in request and request.allowed_html_tags or "h1, h2, p",
                        style_requirements="style_requirements" in request and request.style_requirements or "professional"
                    ) 

                    full_content = ""

                    # status response
        
                    logging.debug("Creating content strategy")
                    yield "data: "+json.dumps(routellm.models.create_status_response_dict("Creating content strategy", 1, 3, "planning")) + "\n\n"
                    content_strategy = await app.controllers.longwriter.get_content_strategy(content_request, model)
                    logging.debug("Creating HTML strategy")
                    yield "data: "+json.dumps(routellm.models.create_status_response_dict("Creating HTML strategy", 2, 3, "planning")) + "\n\n"
                    html_strategy = await app.controllers.longwriter.get_html_strategy(content_request.allowed_html_tags, content_strategy, model)
                    logging.debug("Creating Content outline")
                    yield "data: "+json.dumps(routellm.models.create_status_response_dict("Creating Content outline", 3, 3, "planning")) + "\n\n"
                    content_outline = await app.controllers.longwriter.get_content_outline(content_strategy, html_strategy, model)
                    
                    for _, section in enumerate(content_outline.sections):
                        async for token in app.controllers.longwriter.get_content_draft(
                            section, 
                            content_strategy, 
                            html_strategy, 
                            content_request.style_requirements, 
                            content_outline, 
                            full_content, 
                            model,
                            chunk_size=DEFAULT_CHUNK_SIZE
                        ):
                            full_content += token
                            async for chunk in routellm.models.create_stream_response({"content": token, "model": model}):
                                yield chunk
                except Exception as e:
                    yield f"Error during streaming: {str(e)}"
            return StreamingResponse(iter_response(), media_type="text/event-stream", headers={"X-Chosen-Model": model})  
        else:
            res = app.controllers.completion.completion(**request.model_dump(exclude_none=True))
            is_predefined = isinstance(res, dict) and res.get('model') == 'predefined_prompt'
            chosen_model = res['model'] if is_predefined else res.model
            return StreamingResponse(routellm.models.create_stream_response(res), media_type="text/event-stream", headers={"X-Chosen-Model": chosen_model}) 
    else:
        res = await app.controllers.response(request, controller_name, "acompletion")
        # print(json.dumps(res))
        is_predefined = isinstance(res, dict) and res.get('model') == 'predefined_prompt'
        chosen_model = res['model'] if is_predefined else res.model_dump()['model']

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