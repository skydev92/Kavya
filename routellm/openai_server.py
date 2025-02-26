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
import time
import shortuuid
import re
import subprocess
import socket

import logging
import fastapi
import uvicorn
import litellm

from fastapi.concurrency import asynccontextmanager
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends, BackgroundTasks

from routellm.controller import Controllers, RoutingError, ContentRequest, DEFAULT_CHUNK_SIZE, RequestCostTracker
from routellm.routers.routers import ROUTER_CLS
from routellm.auth import JWTBearer
from routellm.models import InsufficientTokensError
import routellm.models 
from routellm.database import Database, DEFAULT_VALIDATION_INTERVAL

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

# Removed periodic database health check

@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    """Initialize and cleanup application state"""
    
    try:
        # Load config
        config = yaml.safe_load(open(args.config, "r")) if args.config else None
        
        # Get default model pair from config or command line arguments
        default_strong_model = args.strong_model
        default_weak_model = args.weak_model
        
        # If config has model_pairs section, use it to override defaults
        if config and "model_pairs" in config and "default" in config["model_pairs"]:
            default_strong_model = config["model_pairs"]["default"].get("strong", default_strong_model)
            default_weak_model = config["model_pairs"]["default"].get("weak", default_weak_model)
        
        app.controllers = Controllers(
            routers=args.routers,
            config=config,
            strong_model=default_strong_model,
            weak_model=default_weak_model,
            api_base=args.base_url,
            api_key=args.api_key,
            progress_bar=True,
        )
        app.controllers.create_controller("completion", 
            routers=args.routers,
            config=config,
            strong_model=default_strong_model,
            weak_model=default_weak_model,
            api_base=args.base_url,
            api_key=args.api_key,
            progress_bar=True,
        )
        app.controllers.create_controller("longwriter", 
            routers=args.routers,
            config=config,
            strong_model=default_strong_model,
            weak_model=default_weak_model,
            api_base=args.base_url,
            api_key=args.api_key,
            progress_bar=True,
        )
        logging.debug("Default controllers based on arguments, initialized successfully")
        
        # Store model pairs from config for later use
        app.model_pairs = config.get("model_pairs", {}) if config else {}
        
        # Initialize database
        app.db = Database()
        
        # Test database connection by trying to create tables
        try:
            # Initialize the database (creates tables if needed)
            app.db.initialize_database()
            
            # Removed health check task initialization
            
            yield
        except Exception as e:
            logging.error(f"Database initialization failed: {type(e).__name__}: {str(e)}")
            raise Exception("Application startup failed - database initialization error") from e
        
    except Exception as e:
        logging.error(f"Failed to initialize application: {type(e).__name__}: {str(e)}")
        raise Exception("Application startup failed") from e
    
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

@app.get("/health/db")
async def health_db():
    """Check database health and connection status"""
    try:
        # Get database connection
        db = app.db
        
        # Check if Cloud SQL Proxy is running (if applicable)
        cloud_sql_proxy_running = False
        if os.getenv("INSTANCE_CONNECTION_NAME"):
            try:
                # Check if cloud_sql_proxy process is running
                result = subprocess.run(
                    ["pgrep", "-f", "cloud_sql_proxy"], 
                    capture_output=True, 
                    text=True
                )
                cloud_sql_proxy_running = result.returncode == 0
                
                if not cloud_sql_proxy_running:
                    logging.warning("Cloud SQL Proxy does not appear to be running")
            except Exception as e:
                logging.error(f"Error checking Cloud SQL Proxy status: {str(e)}")
        
        # Check if port 5432 is open
        port_open = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(('127.0.0.1', 5432))
                port_open = result == 0
                
            if not port_open:
                logging.warning("PostgreSQL port 5432 is not open")
        except Exception as e:
            logging.error(f"Error checking PostgreSQL port status: {str(e)}")
        
        # Validate database connection using a simple query
        connection = db.get_validated_connection()
        cursor = connection.get_cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
            
        # Get connection pool stats if available
        pool_stats = {}
        if hasattr(db, '_local') and hasattr(db._local, 'db') and hasattr(db._local.db, 'engine'):
            engine = db._local.db.engine
            if hasattr(engine, 'pool'):
                pool = engine.pool
                pool_stats = {
                    "pool_size": getattr(pool, 'size', None),
                    "pool_overflow": getattr(pool, 'overflow', None),
                    "pool_checked_out": getattr(pool, 'checkedout', None),
                }
            
        # Return health status
        return {
            "status": "healthy",
            "message": "Database connection is working properly",
            "timestamp": datetime.now().isoformat(),
            "query_result": result[0] if result else None,
            "environment": os.getenv("ENVIRONMENT", "unknown"),
            "instance_connection_name": os.getenv("INSTANCE_CONNECTION_NAME", "N/A"),
            "cloud_sql_proxy": {
                "running": cloud_sql_proxy_running,
                "port_open": port_open
            },
            "connection_pool": pool_stats
        }
    except Exception as e:
        logging.error(f"Database health check failed: {type(e).__name__}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "unhealthy",
                "message": f"Database connection failed: {str(e)}",
                "timestamp": datetime.now().isoformat(),
                "error_type": type(e).__name__,
                "environment": os.getenv("ENVIRONMENT", "unknown"),
            }
        )

@app.get("/v1/account/balance")
async def get_account_balance(user_id: int = Depends(JWTBearer())):
    """Get account balance for the authenticated user."""
    logging.info(f"Account balance check for user {user_id}")
    
    try:
        # Get account balance
        balance = app.db.get_account_balance_model(account_id=user_id)
        
        # Get daily usage for the current month
        today = datetime.now()
        start_date = f"{today.year}-{today.month:02d}-01"
        end_date = today.strftime("%Y-%m-%d")
        
        daily_usage = app.db.get_daily_usage_model(
            account_id=user_id,
            start_date=start_date,
            end_date=end_date
        )
        
        # Calculate monthly totals
        monthly_totals = {
            "prompt_tokens": sum(day.daily_token_usage_in for day in daily_usage),
            "completion_tokens": sum(day.daily_token_usage_out for day in daily_usage),
            "word_count": sum(day.daily_word_usage for day in daily_usage),
            "transaction_count": sum(day.transaction_count for day in daily_usage)
        }
        
        return JSONResponse(
            content={
                "account_id": balance.account_id,
                "balance": {
                    "token_balance_in": balance.token_balance_in,
                    "token_balance_out": balance.token_balance_out,
                    "word_balance": balance.word_balance if hasattr(balance, 'word_balance') else 0,
                    "transactions": balance.transactions
                },
                "monthly_usage": {
                    "month": f"{today.year}-{today.month:02d}",
                    "prompt_tokens": monthly_totals["prompt_tokens"],
                    "completion_tokens": monthly_totals["completion_tokens"],
                    "word_count": monthly_totals["word_count"],
                    "transaction_count": monthly_totals["transaction_count"]
                },
                "daily_usage": [day.model_dump() for day in daily_usage]
            },
            status_code=200
        )
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logging.error(f"Error getting account balance: {error_type}: {error_msg}")
        
        return JSONResponse(
            content={
                "error": {
                    "message": f"Failed to retrieve account balance: {error_msg}",
                    "type": error_type,
                    "code": "balance_retrieval_failed"
                }
            },
            status_code=500
        )

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
        # Store original model name before translation
        original_model = kavya_request.model
        # If validation passes, translate the model and store original
        request_data["original_model"] = original_model
        request_data["model"] = app.controllers.default.model_translations[original_model]
        # Store original model in the controller for later use
        app.controllers.default.original_model = original_model
        app.controllers.completion.original_model = original_model
        app.controllers.longwriter.original_model = original_model
        
        # Check if we have a specific model pair for this Kavya model
        if hasattr(app, 'model_pairs') and original_model in app.model_pairs:
            # Get the model pair for this Kavya model
            model_pair = app.model_pairs[original_model]
            logging.info(f"Using model pair for {original_model}: strong={model_pair.get('strong')}, weak={model_pair.get('weak')}")
            
            # Update the model pair in all controllers
            for controller_name in ['default', 'completion', 'longwriter']:
                controller = app.controllers.controllers[controller_name]
                if 'strong' in model_pair:
                    controller.model_pair.strong = model_pair['strong']
                if 'weak' in model_pair:
                    controller.model_pair.weak = model_pair['weak']
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
    estimated_word_count = 100  # Conservative estimate for word count
    
    # Check balance without updating
    try:
        has_sufficient_balance, current_balance = app.db.check_sufficient_balance(
            account_id=user_id,
            prompt_tokens=int(estimated_prompt_tokens),
            completion_tokens=int(estimated_completion_tokens),
            word_count=estimated_word_count
        )
        if not has_sufficient_balance:
            error_msg = (
                f"Insufficient balance. Please email jur@dxpr.com to request more tokens. Current balance: "
                f"{current_balance['token_balance_in']} input tokens, "
                f"{current_balance['token_balance_out']} output tokens, "
                f"{current_balance['word_balance']} words. "
                f"Required: {estimated_prompt_tokens} input tokens, "
                f"{estimated_completion_tokens} output tokens, "
                f"{estimated_word_count} words."
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
                    "X-Current-Balance-In": str(current_balance['token_balance_in']),
                    "X-Current-Balance-Out": str(current_balance['token_balance_out']),
                    "X-Current-Balance-Words": str(current_balance['word_balance']),
                    "X-Required-Tokens-In": str(estimated_prompt_tokens),
                    "X-Required-Tokens-Out": str(estimated_completion_tokens),
                    "X-Required-Words": str(estimated_word_count)
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
            completion_tokens=int(estimated_completion_tokens),
            word_count=estimated_word_count
        )
        if not has_sufficient_balance:
            error_msg = (
                f"Insufficient remaining balance after routing. Please email jur@dxpr.com to request more tokens. Current balance: "
                f"{current_balance['token_balance_in']} input tokens, "
                f"{current_balance['token_balance_out']} output tokens, "
                f"{current_balance['word_balance']} words. "
                f"Required: {remaining_prompt_tokens} input tokens, "
                f"{estimated_completion_tokens} output tokens, "
                f"{estimated_word_count} words."
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
                    "X-Current-Balance-In": str(current_balance['token_balance_in']),
                    "X-Current-Balance-Out": str(current_balance['token_balance_out']),
                    "X-Current-Balance-Words": str(current_balance['word_balance']),
                    "X-Required-Tokens-In": str(remaining_prompt_tokens),
                    "X-Required-Tokens-Out": str(estimated_completion_tokens),
                    "X-Required-Words": str(estimated_word_count)
                }
            )
        
        if request.stream:
            if controller_name.startswith("longwriter"):
                model = request.model or app.controllers.longwriter.get_model(**request.model_dump(exclude_none=True))
                logging.debug("model: " + model)
                async def iter_response():
                    logging.debug("iter_response")
                    try:
                        # First yield router usage information if available
                        if hasattr(request, 'router_usage') and request.router_usage:
                            router_usage_event = {
                                "jsonrpc": "2.0",
                                "method": "agent/cost_disclosure",
                                "params": {
                                    "prompt_tokens": request.router_usage["prompt_tokens"],
                                    "completion_tokens": request.router_usage["completion_tokens"],
                                    "description": "Router analysis"
                                }
                            }
                            yield "data: "+json.dumps(router_usage_event) + "\n\n"

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
                        # Disclose content strategy costs
                        yield "data: "+json.dumps(routellm.models.create_cost_disclosure_dict(
                            prompt_tokens=app.controllers.longwriter.cost_tracker.prompt_tokens,
                            completion_tokens=app.controllers.longwriter.cost_tracker.completion_tokens,
                            description="Content strategy generation"
                        )) + "\n\n"
                        
                        logging.debug("Creating HTML strategy")
                        yield "data: "+json.dumps(routellm.models.create_status_response_dict("Creating HTML strategy", 2, 3, "planning")) + "\n\n"
                        html_strategy = await app.controllers.longwriter.get_html_strategy(content_request.allowed_html_tags, content_strategy, model)
                        # Disclose HTML strategy costs
                        yield "data: "+json.dumps(routellm.models.create_cost_disclosure_dict(
                            prompt_tokens=app.controllers.longwriter.cost_tracker.prompt_tokens,
                            completion_tokens=app.controllers.longwriter.cost_tracker.completion_tokens,
                            description="HTML strategy generation"
                        )) + "\n\n"
                        
                        logging.debug("Creating Content outline")
                        yield "data: "+json.dumps(routellm.models.create_status_response_dict("Creating Content outline", 3, 3, "planning")) + "\n\n"
                        content_outline = await app.controllers.longwriter.get_content_outline(content_strategy, html_strategy, model)
                        # Disclose content outline costs
                        yield "data: "+json.dumps(routellm.models.create_cost_disclosure_dict(
                            prompt_tokens=app.controllers.longwriter.cost_tracker.prompt_tokens,
                            completion_tokens=app.controllers.longwriter.cost_tracker.completion_tokens,
                            description="Content outline generation"
                        )) + "\n\n"
                        
                        # Use async iteration for sections
                        response_id = f"chatcmpl-{shortuuid.random()}"
                        created_time = int(time.time())
                        initial_usage = {
                            "prompt_tokens": app.controllers.longwriter.cost_tracker.prompt_tokens
                        }
                        # Send initial assistant role
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}], 'usage': initial_usage})}\n\n"
                        
                        # Initialize word buffer for tracking total words
                        word_buffer = ""
                        
                        def count_words(text: str) -> int:
                            """Count words in text after stripping HTML tags."""
                            # Remove HTML tags using regex
                            text_without_html = re.sub(r'<[^>]+>', '', text)
                            # Split on whitespace and filter out empty strings
                            words = [word for word in text_without_html.split() if word.strip()]
                            return len(words)

                        for section in content_outline.sections:
                            async for token, token_count in app.controllers.longwriter.get_content_draft(
                                section, 
                                content_strategy, 
                                html_strategy, 
                                content_outline, 
                                model
                            ):
                                # Accumulate content for word counting
                                word_buffer += token
                                current_word_count = count_words(word_buffer)
                                
                                # Send content chunk with word count
                                usage = {
                                    "completion_tokens": token_count,
                                    "word_count": current_word_count
                                }
                                yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': token}, 'finish_reason': None}], 'usage': usage})}\n\n"
                        
                        # Update database with final word count
                        try:
                            final_word_count = count_words(word_buffer)
                            app.db.update_usage_with_response(
                                account_id=int(user_id),
                                prompt_tokens=app.controllers.longwriter.cost_tracker.prompt_tokens,
                                completion_tokens=app.controllers.longwriter.cost_tracker.completion_tokens,
                                word_count=final_word_count
                            )
                        except Exception as e:
                            logging.error(f"Error updating word count in database: {str(e)}", exc_info=True)
                        
                        # Send final stop message
                        yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                        yield "data: [DONE]\n\n"
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
                
                # Remove original_model from kwargs before API call
                original_model = kwargs.pop('original_model', None)
                
                # Get router usage from the request object where it was stored during basic_routing
                router_usage = getattr(request, 'router_usage', None)
                
                # Remove router_usage from kwargs if present
                kwargs.pop('router_usage', None)
                
                # Make the API call asynchronously
                async def generate_stream():
                    try:
                        # First yield router usage information if available
                        if router_usage:
                            router_usage_event = {
                                "jsonrpc": "2.0",
                                "method": "agent/cost_disclosure",
                                "params": {
                                    "prompt_tokens": router_usage["prompt_tokens"],
                                    "completion_tokens": router_usage["completion_tokens"],
                                    "description": "Router analysis"
                                }
                            }
                            yield f"data: {json.dumps(router_usage_event)}\n\n"
                        
                        # Initialize response_id and created_time at the start
                        response_id = f"chatcmpl-{shortuuid.random()}"
                        created_time = int(time.time())
                        
                        # Add router and threshold to kwargs
                        kwargs["router"] = "mf"
                        kwargs["threshold"] = 0.1
                        
                        # Initialize cost tracker
                        app.controllers.completion.user = str(user_id)  # Set user ID for token tracking
                        app.controllers.completion.cost_tracker = RequestCostTracker()  # Initialize cost tracker

                        # Initialize prompt tokens
                        messages_content = " ".join([msg["content"] for msg in kwargs.get("messages", [])])
                        app.controllers.completion.cost_tracker.prompt_tokens = len(messages_content.split())
                        
                        # Ensure we're using acompletion for async streaming
                        res = await app.controllers.completion.acompletion(**kwargs)
                        
                        if isinstance(res, str):
                            # Handle string responses directly without streaming
                            model = kwargs.get('model', 'unknown')
                            
                            # Get usage information from the controller
                            usage = {
                                "prompt_tokens": app.controllers.completion.cost_tracker.prompt_tokens,
                                "completion_tokens": app.controllers.completion.cost_tracker.completion_tokens,
                                "total_tokens": app.controllers.completion.cost_tracker.get_total()
                            }
                            
                            # Get current token balance
                            try:
                                current_balance = app.db.get_account_balance(account_id=int(app.controllers.completion.user))
                                if current_balance:
                                    total_spent_input_tokens = float(current_balance['token_balance_in'])
                                    total_spent_output_tokens = float(current_balance['token_balance_out'])
                                    
                                    # First chunk with role and usage
                                    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}], 'usage': usage, 'total_spent_input_tokens': total_spent_input_tokens, 'total_spent_output_tokens': total_spent_output_tokens})}\n\n"
                                    
                                    # Content chunk
                                    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': res}, 'finish_reason': None}], 'usage': usage})}\n\n"
                                    
                                    # Final chunk with finish reason and updated usage
                                    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': usage, 'total_spent_input_tokens': total_spent_input_tokens, 'total_spent_output_tokens': total_spent_output_tokens})}\n\n"
                            except Exception as e:
                                logging.error(f"Error getting token balance: {str(e)}")
                                # Yield chunks without balance information
                                yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}], 'usage': usage})}\n\n"
                                yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': res}, 'finish_reason': None}], 'usage': usage})}\n\n"
                                yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': usage})}\n\n"
                        else:
                            # Handle streaming responses
                            async for chunk in routellm.models.create_stream_response(res, controller=app.controllers.completion, completion_tokens=1):
                                yield chunk
                            
                            # Update token usage in database
                            try:
                                app.db.update_usage_with_response(
                                    account_id=int(app.controllers.completion.user),
                                    prompt_tokens=app.controllers.completion.cost_tracker.prompt_tokens,
                                    completion_tokens=app.controllers.completion.cost_tracker.completion_tokens
                                )
                            except Exception as e:
                                logging.error(f"Error updating token balance: {str(e)}", exc_info=True)
                        
                        yield "data: [DONE]\n\n"
                            
                    except Exception as e:
                        error_msg = f"Error during streaming: {str(e)}"
                        logging.error(error_msg, exc_info=True)
                        yield f"data: {json.dumps({'error': {'message': error_msg}})}\n\n"
                        yield "data: [DONE]\n\n"

                return StreamingResponse(
                    generate_stream(),
                    media_type="text/event-stream",
                )
        else:
            # Handle non-streaming case
            kwargs = request.model_dump(exclude_none=True)
            kwargs["user"] = str(user_id)  # Ensure user ID is set
            
            # Remove original_model and router_usage from kwargs before API call
            original_model = kwargs.pop('original_model', None)
            kwargs.pop('router_usage', None)
            
            res = await app.controllers.response(request, controller_name, "acompletion", user=str(user_id))
            
            is_predefined = isinstance(res, dict) and res.get('model') == 'predefined_prompt'
            chosen_model = res['model'] if is_predefined else res.model_dump()['model']

            # Get current token balance
            try:
                current_balance = app.db.get_balance(account_id=user_id)
                if is_predefined:
                    content = routellm.models.predefined_completion_response(res, controller=app.controllers.completion).model_dump()
                    content['total_spent_input_tokens'] = float(current_balance['token_balance_in'])
                    content['total_spent_output_tokens'] = float(current_balance['token_balance_out'])
                else:
                    content = res.model_dump()
                    content['total_spent_input_tokens'] = float(current_balance['token_balance_in'])
                    content['total_spent_output_tokens'] = float(current_balance['token_balance_out'])
            except Exception as e:
                logging.error(f"Error getting token balance: {str(e)}")
                if is_predefined:
                    content = routellm.models.predefined_completion_response(res, controller=app.controllers.completion).model_dump()
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
parser.add_argument("--workers", type=int, default=2)
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
parser.add_argument(
    "--strong-model", 
    type=str, 
    default=None,
    help="The strong model to use (can be overridden by config.yaml)"
)
parser.add_argument(
    "--weak-model", 
    type=str, 
    default=None,
    help="The weak model to use (can be overridden by config.yaml)"
)
args = parser.parse_args()

if args.verbose:
    logging.basicConfig(level=logging.INFO)

if not asyncio.get_event_loop().is_running():
    print("Launching server with routers:", args.routers)
    config = uvicorn.Config(
        "routellm.openai_server:app",
        port=args.port,
        host="0.0.0.0",
        workers=args.workers
    )
    server = uvicorn.Server(config)
    server.run()