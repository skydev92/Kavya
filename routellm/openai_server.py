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
import signal

import logging
import fastapi
import uvicorn
import litellm

from fastapi.concurrency import asynccontextmanager
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends

from routellm.controller import Controllers, RoutingError, ContentRequest, DEFAULT_CHUNK_SIZE, RequestCostTracker
from routellm.routers.routers import ROUTER_CLS
from routellm.auth import JWTBearer
from routellm.models import InsufficientTokensError
import routellm.models 
from routellm.database import Database

from dotenv import load_dotenv

# ------------------------------------------------------------------------------
# APPLICATION INITIALIZATION
# ------------------------------------------------------------------------------

def signal_handler(signum, frame):
    """Handle interrupt signals by outputting total cost before exit."""
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    """Initialize and cleanup application state"""
    try:
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
        logging.debug("Default controllers based on arguments, initialized successfully")
        
        # Initialize database
        app.db = Database()
        
        # Test database connection by trying to create tables
        try:
            # Get a test connection to verify database is working
            test_conn = app.db._get_connection()
            cursor = test_conn.get_cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            yield
        except Exception as e:
            logging.error(f"Database connection test failed: {str(e)}")
            raise Exception("Application startup failed - database initialization error") from e
        
    except Exception as e:
        logging.error(f"Failed to initialize application: {str(e)}")
        raise Exception("Application startup failed - database initialization error") from e
    
    finally:
        # Cleanup on shutdown
        if hasattr(app, 'db') and app.db:
            app.db.close()
        if hasattr(app, 'controllers'):
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
async def create_chat_completion(request_data: dict = fastapi.Body(...), user_id: int = Depends(JWTBearer())):
    # Validate and translate Kavya models before creating the ChatCompletionRequest
    if "model" not in request_data:
        return JSONResponse(
            content={
                "error": {
                    "message": "Missing required field: model",
                    "type": "invalid_request_error",
                    "param": "model",
                    "code": "missing_field"
                }
            },
            status_code=400
        )

    # First validate if it's a Kavya request
    try:
        kavya_request = routellm.models.KavyaRequest(**request_data)
        # If validation passes, translate the model
        request_data["model"] = app.controllers.default.model_translations[kavya_request.model]
    except Exception as e:
        # Only return Kavya validation error if it's a Kavya model
        if "model" in request_data and isinstance(request_data["model"], str) and request_data["model"].startswith("kavya-"):
            return JSONResponse(
                content={
                    "error": {
                        "message": f"Invalid Kavya model. Must be one of: {', '.join(app.controllers.default.model_translations.keys())}",
                        "type": "invalid_request_error",
                        "param": "model",
                        "code": "invalid_model"
                    }
                },
                status_code=400
            )

    # Now create the ChatCompletionRequest with the translated model
    try:
        request = routellm.models.ChatCompletionRequest(**request_data)
    except Exception as e:
        return JSONResponse(
            content={
                "error": {
                    "message": str(e),
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "validation_error"
                }
            },
            status_code=400
        )

    logging.info(f"Received request: {request}")
    
    # Ensure user_id is set in the request
    request_dict = request.model_dump(exclude_none=True)
    request_dict["user"] = str(user_id)  # Convert to string as that's what the API expects
    request = routellm.models.ChatCompletionRequest(**request_dict)
    
    # Get token estimates for the request
    messages_content = " ".join([msg["content"] for msg in request.messages])
    estimated_prompt_tokens = len(messages_content.split()) * 1.5  # Rough estimate
    estimated_completion_tokens = 500  # Conservative estimate for completion
    
    # Check balance without updating
    try:
        has_sufficient_balance, current_balance = app.db.check_sufficient_balance(
            account_id=user_id,
            prompt_tokens=int(estimated_prompt_tokens),
            completion_tokens=int(estimated_completion_tokens)
        )
        if not has_sufficient_balance:
            error_msg = (
                f"Insufficient token balance. Current balance: "
                f"{current_balance['token_in']} input tokens, "
                f"{current_balance['token_out']} output tokens. "
                f"Required: {estimated_prompt_tokens} input tokens, "
                f"{estimated_completion_tokens} output tokens."
            )
            logging.error(f"Account {user_id}: {error_msg}")
            return JSONResponse(
                content={
                    "error": {
                        "message": error_msg,
                        "type": "insufficient_balance",
                        "param": None,
                        "code": "insufficient_tokens"
                    }
                },
                status_code=402,  # Payment Required
                headers={
                    "X-Current-Balance-In": str(current_balance['token_in']),
                    "X-Current-Balance-Out": str(current_balance['token_out']),
                    "X-Required-Tokens-In": str(estimated_prompt_tokens),
                    "X-Required-Tokens-Out": str(estimated_completion_tokens)
                }
            )
    except Exception as e:
        error_msg = f"Error checking token balance: {str(e)}"
        logging.error(error_msg)
        return JSONResponse(
            content={
                "error": {
                    "message": error_msg,
                    "type": "internal_error",
                    "param": None,
                    "code": "balance_check_failed"
                }
            },
            status_code=500
        )
    
    # Create a cost tracker for this request
    cost_tracker = RequestCostTracker()
    
    try:
        # First determine routing - this will use some tokens
        controller_name = await app.controllers.basic_routing(request, cost_tracker)
        logging.debug("controller_name: " + controller_name)
        
        # After routing, check remaining balance without updating
        routing_cost = cost_tracker.get_total()
        remaining_prompt_tokens = estimated_prompt_tokens - routing_cost
        
        has_sufficient_balance, current_balance = app.db.check_sufficient_balance(
            account_id=user_id,
            prompt_tokens=int(remaining_prompt_tokens),
            completion_tokens=int(estimated_completion_tokens)
        )
        if not has_sufficient_balance:
            error_msg = (
                f"Insufficient remaining token balance after routing. Current balance: "
                f"{current_balance['token_in']} input tokens, "
                f"{current_balance['token_out']} output tokens. "
                f"Required: {remaining_prompt_tokens} input tokens, "
                f"{estimated_completion_tokens} output tokens."
            )
            logging.error(f"Account {user_id}: {error_msg}")
            return JSONResponse(
                content={
                    "error": {
                        "message": error_msg,
                        "type": "insufficient_balance",
                        "param": None,
                        "code": "insufficient_tokens"
                    }
                },
                status_code=402,
                headers={
                    "X-Current-Balance-In": str(current_balance['token_in']),
                    "X-Current-Balance-Out": str(current_balance['token_out']),
                    "X-Required-Tokens-In": str(remaining_prompt_tokens),
                    "X-Required-Tokens-Out": str(estimated_completion_tokens)
                }
            )
        
        if request.stream:
            if controller_name.startswith("longwriter"):
                model = request.model or app.controllers.longwriter.get_model(**request.model_dump(exclude_none=True))
                logging.debug("model: " + model)
                async def iter_response():
                    logging.debug("iter_response")
                    try:
                        # Default HTML tags
                        allowed_html_tags = "a, blockquote, code, em, figcaption, h1, h2, h3, img, li, ol, p, pre, strong, table, td, tr, ul"
                        # Check if custom tags are provided in request
                        if request.allowed_html_tags is not None:
                            allowed_html_tags = request.allowed_html_tags
                            logging.debug(f"Using custom HTML tags: {allowed_html_tags}")
                        
                        content_request = ContentRequest(
                            prompt=request.messages[-1]["content"],
                            allowed_html_tags=allowed_html_tags,
                            messages=request.messages,
                            user=str(user_id)  # Add user ID to content request
                        ) 

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
                                content_outline, 
                                model
                            ):
                                async for chunk in routellm.models.create_stream_response({"content": token, "model": model}):
                                    yield chunk
                    except Exception as e:
                        error_msg = f"Error during streaming: {str(e)}"
                        logging.error(error_msg)
                        yield f"data: {json.dumps({'error': {'message': error_msg}})}\n\n"
                return StreamingResponse(iter_response(), media_type="text/event-stream", headers={"X-Chosen-Model": model})
            else:
                # Use the routed model if available, otherwise use completion's default model
                if request.model:
                    kwargs = request.model_dump(exclude_none=True)
                else:
                    kwargs = request.model_dump(exclude_none=True)
                    kwargs["model"] = app.controllers.completion.model_pair.weak
                
                logging.info("calling app.controllers.completion.completion for non-longwriter response")
                
                # Ensure user ID is set
                kwargs["user"] = str(user_id)
                
                res = app.controllers.completion.completion(**kwargs)
                
                is_predefined = isinstance(res, dict) and res.get('model') == 'predefined_prompt'
                chosen_model = res['model'] if is_predefined else res.model

                return StreamingResponse(
                    routellm.models.create_stream_response(res),
                    media_type="text/event-stream",
                )
        else:
            # Handle non-streaming case
            kwargs = request.model_dump(exclude_none=True)
            kwargs["user"] = str(user_id)  # Ensure user ID is set
            res = await app.controllers.response(request, controller_name, "acompletion", user=str(user_id))
            
            is_predefined = isinstance(res, dict) and res.get('model') == 'predefined_prompt'
            chosen_model = res['model'] if is_predefined else res.model_dump()['model']

            if is_predefined:
                content = routellm.models.predefined_completion_response(res).model_dump()
            else:
                content = res.model_dump()
                
            return JSONResponse(content=content, headers={"X-Chosen-Model": chosen_model})
            
    except Exception as e:
        error_msg = f"Error processing request: {str(e)}"
        logging.error(error_msg)
        return JSONResponse(
            content={
                "error": {
                    "message": error_msg,
                    "type": "internal_error",
                    "param": None,
                    "code": "request_failed"
                }
            },
            status_code=500
        )

# ------------------------------------------------------------------------------
# MAIN : APPLICATION STARTUP
# ------------------------------------------------------------------------------

# Configure litellm to drop unsupported parameters
litellm.drop_params = True

# Configure logging
logging.basicConfig(
    level=logging.INFO,
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
        port=args.port,
        host="0.0.0.0",
        workers=args.workers,
    )