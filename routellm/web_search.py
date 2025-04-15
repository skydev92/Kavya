import os
import requests
import logging
import re
from typing import List

from routellm.models import WebSearchResult, ConfidenceEvaluation

async def evaluate_confidence(controller, messages) -> ConfidenceEvaluation:
    """Evaluate model confidence using the weak model."""
    last_user_message = next((m["content"] for m in reversed(messages) 
                             if m["role"] == "user"), "")
    
    prompt = f"How sure are you that you can deliver an up-to-date and fact-checked completion for this task? Answer with a number between 0 and 100.\n\nTask: {last_user_message}"
    
    try:
        response = await controller.acompletion(
            model=controller.model_pair.weak,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.1
        )
        
        content = response["choices"][0]["message"]["content"].strip()
        match = re.search(r'\b(\d+)\b', content)
        score = int(match.group(1)) if match else 70
        # Hardcoded threshold of 70
        return ConfidenceEvaluation(score=score, search_required=score < 70)
    except Exception as e:
        logging.error(f"Confidence evaluation error: {str(e)}")
        return ConfidenceEvaluation(score=100, search_required=False)

async def search_web(query: str, api_key: str) -> List[WebSearchResult]:
    """Perform a web search using Brave Search API."""
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    params = {"q": query, "count": 1}
    
    try:
        response = requests.get(url, headers=headers, params=params)
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
    except Exception as e:
        logging.error(f"Web search error: {str(e)}")
        return [] 