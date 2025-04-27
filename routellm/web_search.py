import os
import logging
import re
import httpx
import asyncio
import json
import yaml
import litellm
from typing import List, Optional
from datetime import datetime

from routellm.models import WebSearchEvaluation

def get_web_search_config():
    """Get web search configuration from config.yaml. Requires explicit threshold setting."""
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
            
        if "web_search" not in config:
            raise ValueError("web_search configuration section is missing from config.yaml")
            
        web_search_config = config["web_search"]
        if "need_threshold" not in web_search_config:
            raise ValueError("need_threshold is required in web_search configuration")
            
        return web_search_config
            
    except Exception as e:
        logging.error(f"WEB_SEARCH: Failed to load required web search configuration: {str(e)}")
        raise RuntimeError(f"Web search configuration error: {str(e)}")

async def enhance_with_web_search(controller, messages):
    """Add web search results to messages if web search need is above threshold."""
    # Check if the Perplexity API key is set for LiteLLM to use
    perplexity_api_key_present = os.environ.get("PERPLEXITYAI_API_KEY") is not None
    logging.info(f"WEB_SEARCH: Perplexity API key present for LiteLLM: {perplexity_api_key_present}")
    if not perplexity_api_key_present:
        return messages # Skip if key is missing

    try:
        # Evaluate if web search would help
        eval_result = await evaluate_web_search_need(controller, messages)
        if not eval_result.search_required:
            return messages
        
        # Extract user query for search
        user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        
        # Generate optimized search query using LLM
        search_query = await generate_search_query(controller, user_msg)
        logging.info(f"WEB_SEARCH: Generated optimized search query: '{search_query}'")
        
        # Perform web search using Perplexity via LiteLLM
        logging.info(f"PERPLEXITY_SEARCH: Searching for: '{search_query}'")
        # Pass only the query, LiteLLM handles the API key from env
        search_results = await search_web(search_query)
        
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
        logging.error(f"WEB_SEARCH: Error during Perplexity search enhancement: {str(e)}")
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

async def evaluate_web_search_need(controller, messages) -> WebSearchEvaluation:
    """Evaluate how much web search would help with answering this request."""
    last_user_message = next((m["content"] for m in reversed(messages) 
                             if m["role"] == "user"), "")
    
    # Get web search threshold from config
    config = get_web_search_config()
    threshold = config["need_threshold"]
    
    # Include current date in prompt
    current_date = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""Today's date is {current_date}.
Rate how much this request requires web search for accurate and up-to-date information.
Task: {last_user_message}

Respond with a JSON object containing a single field:
'score': Number between 0-100 indicating how much web search would help (0=not needed, 100=definitely needed)

Examples:
- For "What is 2+2?": {{"score": 0}}
- For "What are the latest AI developments?": {{"score": 85}}"""
    
    # Get user ID for tracking
    user_id = next((m.get("user") for m in messages if isinstance(m, dict) and "user" in m), None)
    if not user_id and hasattr(controller, "user"):
        user_id = controller.user
    user_id = user_id or "system_web_search"
    
    # Use litellm directly to avoid recursion
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
                raise ValueError(f"Web search need score {score} must be between 0 and 100")
            
            # Create evaluation and compute search_required based on threshold
            evaluation = WebSearchEvaluation(score=score)
            evaluation.compute_search_required(threshold)
            return evaluation
            
        raise ValueError("Missing required score field in JSON response")
        
    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"WEB_SEARCH: Failed to parse web search need evaluation: {str(e)}")
        raise RuntimeError(f"Failed to evaluate web search need: {str(e)}")

async def search_web(query: str) -> Optional[str]:
    """Search the web using Perplexity AI API via LiteLLM and return text response."""
    logging.info(f"PERPLEXITY_SEARCH: Searching for: '{query}' via LiteLLM")

    model_name = "perplexity/sonar-pro" # Using sonar-pro as requested
    messages = [
        # Optional: Add system prompt if desired/recommended by Perplexity for search
        # {"role": "system", "content": "Provide concise and factual search results."},
        {"role": "user", "content": query}
    ]

    try:
        # LiteLLM handles retries based on its configuration.
        # Add other relevant LiteLLM parameters if needed (e.g., temperature=0.3)
        response = await litellm.acompletion(
            model=model_name,
            messages=messages,
            # temperature=0.3, # Example optional parameter
            # max_tokens=1000 # Example optional parameter
        )

        # Extract result (check LiteLLM response structure - assuming standard format)
        if response and response.get("choices") and response["choices"][0].get("message"):
            result_text = response["choices"][0]["message"]["content"].strip()
        else:
            logging.warning(f"PERPLEXITY_SEARCH: Received unexpected response structure from LiteLLM: {response}")
            return None

        # Limit text length if necessary
        if len(result_text) > 200000:
            result_text = result_text[:200000]
            logging.info(f"PERPLEXITY_SEARCH: Truncated response text to 200000 characters")

        logging.info(f"PERPLEXITY_SEARCH: Successfully retrieved search results ({len(result_text)} chars)")
        return result_text

    except Exception as e:
        # Handle potential exceptions from LiteLLM
        # Consider specific exception types based on LiteLLM docs if available
        # (e.g., litellm.exceptions.AuthenticationError, litellm.exceptions.RateLimitError)
        logging.error(f"PERPLEXITY_SEARCH: LiteLLM call failed: {str(e)}")
        return None # Propagate failure 