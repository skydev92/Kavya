import os
import requests
import logging
import re
from typing import List
from datetime import datetime
import httpx
import asyncio
import random

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

async def generate_search_queries(controller, user_msg: str) -> List[str]:
    """Use the weak LLM to generate 3 distinct search queries."""
    logging.info(f"WEB_SEARCH: Generating creative search queries for: '{user_msg[:100]}...'")
    
    # Clean the base message for the prompt to the generator LLM
    base_query_for_prompt = clean_search_query(user_msg)
    
    prompt = f"""Based on the following user task, generate exactly 3 distinct and creative search engine queries. The queries should explore different facets of the topic. Make one query explore a surprising or less obvious angle.

User Task: {base_query_for_prompt}

Return the queries as a JSON object with a single key "queries" containing a list of 3 strings.
Example:
{{
  "queries": [
    "query exploring main topic",
    "query exploring a related aspect",
    "query exploring a surprising angle"
  ]
}}
"""
    
    # Get user ID for tracking (can reuse logic from evaluate_confidence)
    # For simplicity here, let's assume we get it from the controller or default
    user_id = getattr(controller, 'user', 'system_query_generator')
    
    fallback_queries = [
        base_query_for_prompt,
        f"{base_query_for_prompt} current details",
        f"{base_query_for_prompt} overview"
    ]

    try:
        import litellm
        response = await litellm.acompletion(
            model=controller.model_pair.weak,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150, # More tokens needed for 3 queries
            temperature=0.5, # Allow some creativity
            response_format={"type": "json_object"},
            user=user_id
        )
        
        content = response["choices"][0]["message"]["content"].strip()
        
        import json
        json_data = json.loads(content)
        
        if "queries" in json_data and isinstance(json_data["queries"], list) and len(json_data["queries"]) == 3:
            generated_queries = [str(q) for q in json_data["queries"]]
            logging.info(f"WEB_SEARCH: Successfully generated queries: {generated_queries}")
            return generated_queries
        else:
            logging.warning(f"WEB_SEARCH: LLM did not return 3 queries in expected format. Response: {content}")
            return fallback_queries
            
    except (json.JSONDecodeError, ValueError, KeyError, IndexError, Exception) as e:
        logging.error(f"WEB_SEARCH: Failed to generate/parse search queries: {str(e)}")
        return fallback_queries

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
    MAX_QUERY_LENGTH = 150
    if len(query) > MAX_QUERY_LENGTH:
        query = query[:MAX_QUERY_LENGTH]
        
    return query

async def search_web(query: str, api_key: str) -> List[WebSearchResult]:
    """Get top URL from Brave, fetch full content via Jina Reader."""
    # Clean the query for Brave Search - REMOVED, cleaning is done in controller
    # clean_query = clean_search_query(query)
    # logging.info(f"WEB_SEARCH: Cleaned query for Brave: '{clean_query}'")
    logging.info(f"WEB_SEARCH: Received query for Brave: '{query}'") # Log received query
    
    brave_url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    params = {"q": query, "count": 1} # Use received query directly
    
    # --- Get URL from Brave (with retry) ---
    max_retries = 3
    base_delay = 1
    original_url = None
    title = ""
    
    for attempt in range(max_retries):
        try:
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            if attempt > 0:
                logging.info(f"BRAVE_SEARCH: Retry attempt {attempt+1}/{max_retries} after {delay:.2f}s delay")
                await asyncio.sleep(delay)
                
            # Use httpx for async request to Brave
            async with httpx.AsyncClient() as client:
                response = await client.get(brave_url, headers=headers, params=params)
                
            status_code = response.status_code
            if status_code == 429:
                logging.warning(f"BRAVE_SEARCH: Rate limited (429), will retry in {delay:.2f}s")
                continue
            
            response.raise_for_status()
            data = response.json()
            
            if "web" in data and "results" in data["web"] and data["web"]["results"]:
                top_result = data["web"]["results"][0]
                original_url = top_result.get("url")
                title = top_result.get("title", "")
                if original_url:
                    logging.info(f"BRAVE_SEARCH: Found URL: {original_url}")
                    break # Exit retry loop on success
                else:
                    logging.warning("BRAVE_SEARCH: Top result missing URL.")
            else:
                 logging.warning("BRAVE_SEARCH: No results found.")
                 return [] # No results, return empty

        except httpx.RequestError as e:
             if attempt == max_retries - 1:
                 logging.error(f"BRAVE_SEARCH: Failed after {max_retries} attempts: {str(e)}")
                 return []
        except Exception as e:
             logging.error(f"BRAVE_SEARCH: Unexpected error: {str(e)}")
             return []

    if not original_url:
        logging.warning("BRAVE_SEARCH: Could not retrieve a valid URL after retries.")
        return []

    # --- Fetch full content using Jina Reader (with retry) ---
    jina_url = f"https://r.jina.ai/{original_url}"
    logging.info(f"JINA_READER: Fetching content from: {jina_url}")

    for attempt in range(max_retries):
        try:
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            if attempt > 0:
                logging.info(f"JINA_READER: Retry attempt {attempt+1}/{max_retries} after {delay:.2f}s delay")
                await asyncio.sleep(delay)

            async with httpx.AsyncClient(timeout=30.0) as client: # Added timeout
                 jina_response = await client.get(jina_url)

            status_code = jina_response.status_code
            # Jina might use different codes, but 429 is common
            if status_code == 429:
                 logging.warning(f"JINA_READER: Rate limited (429), will retry in {delay:.2f}s")
                 continue
            
            jina_response.raise_for_status()
            full_content = jina_response.text
            logging.info(f"JINA_READER: Successfully fetched full content ({len(full_content)} chars)")
            
            # Use the 'summary' field to hold the full content
            return [WebSearchResult(title=title, url=original_url, summary=full_content)]

        except httpx.RequestError as e:
             # Log specific Jina errors differently
             if attempt == max_retries - 1:
                 logging.error(f"JINA_READER: Failed after {max_retries} attempts: {str(e)}")
                 return []
        except Exception as e:
             logging.error(f"JINA_READER: Unexpected error: {str(e)}")
             return []

    logging.warning("JINA_READER: Failed to fetch content after retries.")
    return [] # Return empty if all Jina retries fail 