import os
import requests
import logging
import re
import time
import random
import asyncio
from typing import List
from datetime import datetime

from routellm.models import WebSearchResult, ConfidenceEvaluation

async def evaluate_confidence(controller, messages) -> ConfidenceEvaluation:
    """Evaluate model confidence using the weak model."""
    last_user_message = next((m["content"] for m in reversed(messages) 
                             if m["role"] == "user"), "")
    
    # Include current date in prompt
    current_date = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""Today's date is {current_date}.
Evaluate your confidence in answering this request accurately with up-to-date information.
Task: {last_user_message}

Respond with a JSON object containing:
1. 'score': Number between 0-100 indicating your confidence level
2. 'search_required': Boolean (true/false) indicating if web search would be helpful

Example: {{"score": 85, "search_required": false}}
If you're less than 70% confident, set search_required to true."""
    
    # Get user ID for tracking
    user_id = next((m.get("user") for m in messages if isinstance(m, dict) and "user" in m), None)
    if not user_id and hasattr(controller, "user"):
        user_id = controller.user
    user_id = user_id or "system_web_search"
    
    # Use litellm directly to avoid recursion
    import litellm
    response = await litellm.acompletion(
        model=controller.model_pair.weak,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=50,
        temperature=0.1,
        response_format={"type": "json_object"},
        user=user_id
    )
    
    content = response["choices"][0]["message"]["content"].strip()
    
    import json
    try:
        json_data = json.loads(content)
        
        if "score" in json_data and isinstance(json_data["score"], (int, float)):
            score = int(json_data["score"])
            if score < 0 or score > 100:
                raise ValueError(f"Confidence score {score} must be between 0 and 100")
            
            search_required = json_data.get("search_required", score < 70)
            if not isinstance(search_required, bool):
                search_required = score < 70
                
            return ConfidenceEvaluation(score=score, search_required=search_required)
            
        raise ValueError("Missing required fields in JSON response")
        
    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"WEB_SEARCH: Failed to parse confidence evaluation: {str(e)}")
        raise RuntimeError(f"Failed to evaluate confidence: {str(e)}")

def clean_search_query(query: str) -> str:
    """Clean HTML tags and formatting from search query to get plain text."""
    # Extract text between <TASK> tags if present
    task_match = re.search(r'<TASK>(.*?)</TASK>', query, re.DOTALL)
    if task_match:
        query = task_match.group(1).strip()
    
    # Remove any remaining HTML tags
    query = re.sub(r'<[^>]+>', '', query)
    
    # Remove special markers
    query = re.sub(r'@@@\w+@@@', '', query)
    
    # Clean up extra whitespace
    query = re.sub(r'\s+', ' ', query).strip()
    
    # Limit query length
    if len(query) > 150:
        query = query[:150]
        
    return query

async def search_web(query: str, api_key: str) -> List[WebSearchResult]:
    """Perform a web search using Brave Search API with retry and backoff."""
    # Clean the query
    clean_query = clean_search_query(query)
    logging.info(f"WEB_SEARCH: Cleaned query: '{clean_query}'")
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    params = {"q": clean_query, "count": 1}
    
    # Retry settings
    max_retries = 3
    base_delay = 1  # Base delay in seconds
    
    for attempt in range(max_retries):
        try:
            # Add jitter to avoid thundering herd
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            
            if attempt > 0:
                logging.info(f"WEB_SEARCH: Retry attempt {attempt+1}/{max_retries} after {delay:.2f}s delay")
                await asyncio.sleep(delay)
                
            response = requests.get(url, headers=headers, params=params)
            status_code = response.status_code
            
            # If rate limited, retry with backoff
            if status_code == 429:
                logging.warning(f"WEB_SEARCH: Rate limited (429), will retry in {delay:.2f}s")
                continue
                
            response.raise_for_status()
            data = response.json()
            
            results = []
            if "web" in data and "results" in data["web"]:
                for r in data["web"]["results"][:1]:
                    results.append(WebSearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        summary=r.get("description", "")
                    ))
            return results
            
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:  # Last attempt
                logging.error(f"Web search error after {max_retries} attempts: {str(e)}")
                return []
        except Exception as e:
            logging.error(f"Web search error: {str(e)}")
            return []
    
    return []  # Return empty results if all retries fail 