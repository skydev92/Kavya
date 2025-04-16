import os
import logging
import re
import httpx
import asyncio
import json
from typing import List, Optional
from datetime import datetime

from routellm.models import ConfidenceEvaluation

async def enhance_with_web_search(controller, messages):
    """Add web search results to messages if confidence is below threshold."""
    # Get API key from environment
    jina_api_key = os.environ.get("JINA_API_KEY")
    logging.info(f"WEB_SEARCH: Jina API key present: {jina_api_key is not None}")
    if not jina_api_key:
        return messages
        
    try:
        # Evaluate confidence in answering without search
        eval_result = await evaluate_confidence(controller, messages)
        if not eval_result.search_required:
            return messages
        
        # Extract user query for search
        user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        
        # Generate optimized search query using LLM
        search_query = await generate_search_query(controller, user_msg)
        logging.info(f"WEB_SEARCH: Generated optimized search query: '{search_query}'")
        
        # Perform web search
        logging.info(f"WEB_SEARCH: Searching for: '{search_query}'")
        search_results = await search_web(search_query, jina_api_key)
        
        if not search_results:
            return messages
        
        # Add results in a simple web_search_results tag
        context = f"<web_search_results>\n{search_results}\n</web_search_results>"
        
        # Log the beginning of the context
        logging.info(f"WEB_SEARCH: Adding search context (first 20000 chars):\n{context[:20000]}")

        # Find the last user message and append the context
        new_msgs = messages.copy()
        last_user_msg_index = -1
        for i in range(len(new_msgs) - 1, -1, -1):
            if new_msgs[i]["role"] == "user":
                last_user_msg_index = i
                break
        
        if last_user_msg_index != -1:
            # Prepend the context to the last user message content, separated by newlines
            original_content = new_msgs[last_user_msg_index]["content"]
            new_msgs[last_user_msg_index]["content"] = f"{context}\n\n{original_content}"
            logging.info(f"WEB_SEARCH: Appended search context to last user message at index {last_user_msg_index}")
            return new_msgs
        else:
            # Hard fail if no user message found - don't use fallback
            logging.error("WEB_SEARCH: No user message found to append search context to. Cannot proceed.")
            raise RuntimeError("No user message found in conversation history. Web search results cannot be attached.")
        
    except Exception as e:
        logging.error(f"WEB_SEARCH: Error: {str(e)}")
        return messages

async def generate_search_query(controller, user_msg: str) -> str:
    """Generate an optimized search query using the weak LLM."""
    # Extract text between <TASK> tags if present
    task_match = re.search(r'<TASK>(.*?)</TASK>', user_msg, re.DOTALL)
    if task_match:
        user_msg = task_match.group(1).strip()
    
    # Remove any HTML tags and special markers
    user_msg = re.sub(r'<[^>]+>', '', user_msg)
    user_msg = re.sub(r'@@@\w+@@@', '', user_msg)
    user_msg = re.sub(r'\s+', ' ', user_msg).strip()
    
    # Get current year for time-sensitive queries
    current_year = datetime.now().year
    
    # Create prompt for conceptual search query generation
    prompt = f"""As a search expert, create a conceptual search query that will find relevant information for this question or task. 

Question/Task: "{user_msg}"

Focus on creating a search query that:
1. Captures the conceptual information need rather than literal words
2. Uses broader terms for general understanding of the topic
3. For current events, news, technology, or people in ongoing roles, ADD "{current_year}" to the query
4. When user mentions "current", "latest", "recent", or "now", INCLUDE the year {current_year}
5. For rapidly evolving fields (tech, science, politics, entertainment), ADD the year {current_year}
6. Uses open-ended phrasing that allows for diverse results
7. Removes specific constraints (like word counts, formats, or stylistic requests)
8. Omits action words like "write", "create", "explain", "list" that wouldn't appear in information sources
9. For historical topics or topics with clear time periods, include relevant time frames

Adding the current year {current_year} is IMPORTANT for getting up-to-date information in search results.
Only omit the year for timeless topics like mathematical concepts, established historical events, or fundamental scientific principles.

Return ONLY the search query - no explanation, no formatting, no quote marks.
"""
    
    # Get user ID for tracking
    user_id = getattr(controller, 'user', 'system_query_generator')
    
    try:
        # Use the weak model to generate the query
        import litellm
        response = await litellm.acompletion(
            model=controller.model_pair.weak,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.3,
            user=user_id
        )
        
        # Extract and clean the generated query
        generated_query = response["choices"][0]["message"]["content"].strip()
        
        # Use original user message as fallback if generation fails
        if not generated_query:
            logging.warning(f"WEB_SEARCH: Empty query generated, using original message")
            return user_msg[:150]  # Limit to 150 chars just in case
            
        # Remove any quotes around the entire query if present
        if (generated_query.startswith('"') and generated_query.endswith('"')) or \
           (generated_query.startswith("'") and generated_query.endswith("'")):
            generated_query = generated_query[1:-1]
        
        # Add current year if it's not already in the query and seems like a current topic
        if str(current_year) not in generated_query:
            lower_query = generated_query.lower()
            time_indicators = ["current", "latest", "recent", "now", "today", "this year"]
            topic_indicators = ["news", "update", "trend", "development", "release", "version", "state of"]
            
            # Check if query contains time indicators but no year
            if any(indicator in lower_query for indicator in time_indicators + topic_indicators):
                generated_query = f"{generated_query} {current_year}"
                logging.info(f"WEB_SEARCH: Added year to query: '{generated_query}'")
                
        return generated_query
            
    except Exception as e:
        logging.error(f"WEB_SEARCH: Query generation failed: {str(e)}")
        return user_msg[:150]  # Fallback to truncated original message

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
    import json
    response = await litellm.acompletion(
        model=controller.model_pair.weak,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=50,
        temperature=0.1,
        response_format={"type": "json_object"},
        user=user_id
    )
    
    content = response["choices"][0]["message"]["content"].strip()
    
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

async def search_web(query: str, api_key: str) -> Optional[str]:
    """Search the web using Jina Search API and return markdown text response."""
    logging.info(f"JINA_SEARCH: Searching for: '{query}'")
    
    # Format query for URL
    encoded_query = query.replace(' ', '+')
    jina_url = f"https://s.jina.ai/?q={encoded_query}"
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'X-Engine': 'no-content',
        "X-Retain-Images": "none"
    }
    
    # Set up retry parameters
    max_retries = 3
    base_delay = 1
    
    for attempt in range(max_retries):
        try:
            delay = base_delay * (2 ** attempt) + (asyncio.get_event_loop().time() % 1)  # Add some jitter
            if attempt > 0:
                logging.info(f"JINA_SEARCH: Retry attempt {attempt+1}/{max_retries} after {delay:.2f}s delay")
                await asyncio.sleep(delay)
                
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(jina_url, headers=headers)
                
            status_code = response.status_code
            if status_code == 429:
                logging.warning(f"JINA_SEARCH: Rate limited (429), will retry in {delay:.2f}s")
                continue
                
            response.raise_for_status()
            
            # Get raw text content from response
            result_text = response.text
            
            # Remove markdown links with regex, keeping only the link text
            result_text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', result_text)
            
            # Remove list items (asterisks, dashes, bullets) - both indented and non-indented
            result_text = re.sub(r'^\s*[\*\-•⁃◦▪▫◘○◙♦✓→⟹⟶◆★☆⬧⚫⚪►◄▶◀]\s+.*$', '', result_text, flags=re.MULTILINE)
            
            # Remove list items with numbers or letters (1., a., etc.)
            result_text = re.sub(r'^\s*[\d]+\.\s+.*$', '', result_text, flags=re.MULTILINE)
            result_text = re.sub(r'^\s*[a-zA-Z]\.\s+.*$', '', result_text, flags=re.MULTILINE)
            
            # Remove URL Source and Description lines often found in search results
            result_text = re.sub(r'^\[\d+\]\s+(Title|URL Source|Description):.*$', '', result_text, flags=re.MULTILINE)
            
            # Remove table formatting and info boxes (lines with vertical bars/pipes)
            result_text = re.sub(r'^\s*\|.*\|\s*$', '', result_text, flags=re.MULTILINE)
            result_text = re.sub(r'^\s*\+[-\+]+\s*$', '', result_text, flags=re.MULTILINE)  # Table separator lines
            
            # Remove empty lines created by the previous operations
            result_text = re.sub(r'\n\s*\n', '\n\n', result_text)
            
            # Limit text length if necessary
            if len(result_text) > 200000:
                result_text = result_text[:200000]
                logging.info(f"JINA_SEARCH: Truncated response text to 200000 characters")
            
            logging.info(f"JINA_SEARCH: Successfully retrieved search results ({len(result_text)} chars)")
            return result_text
            
        except httpx.RequestError as e:
            if attempt == max_retries - 1:
                logging.error(f"JINA_SEARCH: Failed after {max_retries} attempts: {str(e)}")
                return None
        except Exception as e:
            logging.error(f"JINA_SEARCH: Unexpected error: {str(e)}")
            if attempt == max_retries - 1:
                return None
    
    logging.warning("JINA_SEARCH: Failed to get search results after retries")
    return None 