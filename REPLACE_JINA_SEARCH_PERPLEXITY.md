# MVP Migration Plan: Jina Search to Perplexity AI

This document outlines the minimum viable steps to replace the Jina Search API with the Perplexity AI API for the web search feature in `routellm/web_search.py`. Functional changes beyond adapting to the new API are out of scope for this MVP.

**Assumptions:**

*   You have access to and have reviewed the latest official Perplexity AI Search API documentation for endpoints, authentication, request/response formats, and error codes.
*   You have obtained a Perplexity AI API key.

**Steps:**

1.  **Configuration Update:**
    *   Add `PERPLEXITY_API_KEY=<your_perplexity_api_key>` to your environment variable sources (e.g., `.env.prod`, `.env.dev`).
    *   Update any template files (e.g., `.env.example`) to include `PERPLEXITY_API_KEY`.

2.  **Modify `routellm/web_search.py`:**

    *   **Update API Key Check in `enhance_with_web_search`:**
        *   Locate the check for `JINA_API_KEY`.
        *   Change it to check for `PERPLEXITY_API_KEY`:
            ```python
            # Replace this:
            # jina_api_key = os.environ.get("JINA_API_KEY")
            # logging.info(f"WEB_SEARCH: Jina API key present: {jina_api_key is not None}")
            # if not jina_api_key:
            #     return messages

            # With this:
            perplexity_api_key = os.environ.get("PERPLEXITY_API_KEY")
            logging.info(f"WEB_SEARCH: Perplexity API key present: {perplexity_api_key is not None}")
            if not perplexity_api_key:
                return messages # Skip if key is missing
            ```
        *   Update the call to `search_web` within the `try` block to pass the `perplexity_api_key`.
        *   Update logging messages within this function from "Jina" to "Perplexity" where relevant.

    *   **Rewrite `search_web` Function:**
        *   **Function Signature:** Update the `api_key` parameter name if desired (e.g., `perplexity_key`).
        *   **Logging:** Change `JINA_SEARCH` log prefixes to `PERPLEXITY_SEARCH`.
        *   **Remove Jina Logic:** Delete the lines constructing the Jina URL (`jina_url = ...`) and the Jina-specific headers (`headers = {'Authorization': ..., 'X-Engine': ..., 'X-Retain-Images': ...}`). Delete the `httpx.get` call.
        *   **Add Perplexity API Call:**
            *   Define `PERPLEXITY_API_ENDPOINT` with the correct URL from their documentation.
            *   Construct the required `headers` for Perplexity (e.g., `{'Authorization': f'Bearer {perplexity_key}', 'Content-Type': 'application/json'}`).
            *   Construct the JSON `payload` according to Perplexity's documentation, including the `query` and necessary model parameters (e.g., `{"model": "...", "messages": [{"role": "user", "content": query}]}`). **Refer to Perplexity docs for the exact structure.**
            *   Make the API call using `httpx.post`:
                ```python
                # Example structure - VERIFY WITH PERPLEXITY DOCS!
                PERPLEXITY_API_ENDPOINT = "https://api.perplexity.ai/chat/completions" # Check docs!
                headers = {
                    'Authorization': f'Bearer {perplexity_key}',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
                payload = {
                    # Consult Perplexity API Docs for required fields
                    "model": "pplx-7b-online", # Example model - check docs
                    "messages": [
                        {"role": "system", "content": "Provide concise and factual information."}, # Optional
                        {"role": "user", "content": query}
                    ]
                }
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(PERPLEXITY_API_ENDPOINT, headers=headers, json=payload)
                ```
        *   **Adapt Error Handling & Retries:**
            *   Review the `try...except` block and the status code checks (`if status_code == 429:`).
            *   Modify the status code checks and associated logging/retry logic to match Perplexity's documented error codes for rate limits, bad requests, server errors, etc.
        *   **Replace Result Processing:**
            *   **Remove all `re.sub(...)` calls** used for cleaning Jina's markdown response.
            *   Parse the JSON response: `json_response = response.json()`.
            *   Extract the relevant search result content from `json_response` based on the structure defined in Perplexity's documentation. This might involve accessing nested fields like `json_response['choices'][0]['message']['content']`. **Verify the exact path.**
            *   Assign the extracted content to `result_text`.
            *   Keep the length truncation logic (`if len(result_text) > 200000:`).

3.  **Testing:**
    *   Perform integration testing locally by sending requests that trigger web search (e.g., current events queries) to your running server.
    *   Verify logs show "PERPLEXITY_SEARCH" messages and successful API calls.
    *   Confirm the format of the results added to the context matches expectations from Perplexity.
    *   Check that the final LLM output utilizes the fetched information.

**Out of Scope for MVP:**

*   Changes to the `evaluate_web_search_need` logic.
*   Changes to the `generate_search_query` logic (unless Perplexity docs strongly recommend a different query style).
*   Advanced result processing beyond extracting the primary content provided by the Perplexity API.
*   Adding new configuration options specific to Perplexity (beyond the API key). 