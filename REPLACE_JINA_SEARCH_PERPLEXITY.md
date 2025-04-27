# MVP Migration Plan: Jina Search to Perplexity AI (using LiteLLM)

This document outlines the minimum viable steps to replace the Jina Search API with the Perplexity AI API for the web search feature in `routellm/web_search.py`, utilizing LiteLLM for the API interaction. Functional changes beyond adapting to the new API and integration method are out of scope for this MVP.

**Assumptions:**

*   You have access to and have reviewed the latest official Perplexity AI Search API documentation and the LiteLLM documentation for Perplexity integration.
*   You have obtained a Perplexity AI API key.
*   LiteLLM is already integrated into the project and configured appropriately for basic usage.

**Steps:**

1.  **Configuration Update:**
    *   Add `PERPLEXITYAI_API_KEY=<your_perplexity_api_key>` to your environment variable sources (e.g., `.env.prod`, `.env.dev`). **Note:** Use the `PERPLEXITYAI_API_KEY` name convention as shown in the LiteLLM docs.
    *   Update any template files (e.g., `.env.example`) to include `PERPLEXITYAI_API_KEY`.

2.  **Modify `routellm/web_search.py`:**

    *   **Import LiteLLM in `search_web`:** Ensure `import litellm` is present if not already file-scoped.
    *   **Update API Key Check in `enhance_with_web_search`:**
        *   Locate the check for `JINA_API_KEY`.
        *   Change it to check for `PERPLEXITYAI_API_KEY`:
            ```python
            # Replace this:
            # jina_api_key = os.environ.get("JINA_API_KEY")
            # logging.info(f"WEB_SEARCH: Jina API key present: {jina_api_key is not None}")
            # if not jina_api_key:
            #     return messages

            # With this:
            # Check if the key is set for LiteLLM to use (no need to pass it explicitly)
            perplexity_api_key_present = os.environ.get("PERPLEXITYAI_API_KEY") is not None
            logging.info(f"WEB_SEARCH: Perplexity API key present for LiteLLM: {perplexity_api_key_present}")
            if not perplexity_api_key_present:
                return messages # Skip if key is missing
            ```
        *   **Remove API key passing:** Remove the `api_key` argument from the call to `search_web` within the `try` block, as LiteLLM picks it up from the environment.
        *   Update logging messages within this function from "Jina" to "Perplexity" where relevant.

    *   **Rewrite `search_web` Function:**
        *   **Function Signature:** Remove the `api_key` (or `perplexity_key`) parameter. The function signature becomes `async def search_web(query: str) -> Optional[str]:`.
        *   **Logging:** Change `JINA_SEARCH` log prefixes to `PERPLEXITY_SEARCH`.
        *   **Remove Jina Logic:** Delete the lines constructing the Jina URL (`jina_url = ...`), Jina-specific headers, and the `httpx.get` call.
        *   **Remove Manual `httpx` Logic:** Delete any remaining manual `httpx.post` logic, header construction, and payload construction intended for Perplexity.
        *   **Add Perplexity API Call via LiteLLM:**
            *   Define the desired Perplexity model using the `perplexity/` prefix (e.g., `model="perplexity/sonar-pro"` or another model suitable for search tasks listed in their docs).
            *   Construct the `messages` payload for LiteLLM.
            *   Call `litellm.acompletion`.
                ```python
                import litellm # Ensure import

                # Example structure - VERIFY MODEL CHOICE & PARAMS
                model_name = "perplexity/sonar-pro" # Choose appropriate model from docs
                messages = [
                    # Optional: Add system prompt if desired/recommended by Perplexity for search
                    # {"role": "system", "content": "Provide concise and factual search results."},
                    {"role": "user", "content": query}
                ]

                try:
                    # Add other relevant LiteLLM parameters if needed (e.g., temperature, max_tokens)
                    # Consult LiteLLM docs for error handling specifics
                    response = await litellm.acompletion(
                        model=model_name,
                        messages=messages,
                        # Add other parameters like temperature=0.3, max_tokens=... if needed
                    )

                    # Extract result (check LiteLLM response structure)
                    result_text = response["choices"][0]["message"]["content"].strip()

                except Exception as e:
                    # Handle potential exceptions from LiteLLM
                    logging.error(f"PERPLEXITY_SEARCH: LiteLLM call failed: {str(e)}")
                    # Consider specific exception types based on LiteLLM docs if available
                    return None # Propagate failure

                ```
        *   **Adapt Error Handling & Retries:**
            *   Remove the manual retry loop (`for attempt in range(max_retries):`). LiteLLM typically handles retries based on its configuration.
            *   Focus the `try...except` block around the `litellm.acompletion` call to catch potential exceptions raised by LiteLLM (e.g., API connection errors, authentication errors, specific Perplexity errors surfaced by LiteLLM). Refer to LiteLLM's documentation for details on exception types.
        *   **Replace Result Processing:**
            *   **Remove all `re.sub(...)` calls** used for cleaning Jina's markdown response.
            *   The result extraction is now done from the LiteLLM `response` object as shown above (`response["choices"][0]["message"]["content"]`). **Verify this path in LiteLLM's standard response schema.**
            *   Keep the length truncation logic (`if len(result_text) > 200000:`).

3.  **Testing:**
    *   Perform integration testing locally by sending requests that trigger web search. Ensure `PERPLEXITYAI_API_KEY` is set in the environment.
    *   Verify logs show "PERPLEXITY_SEARCH" messages and successful calls via LiteLLM.
    *   Confirm the format of the results added to the context matches expectations from Perplexity via LiteLLM.
    *   Check that the final LLM output utilizes the fetched information.

**Out of Scope for MVP:**

*   Changes to the `evaluate_web_search_need` logic.
*   Changes to the `generate_search_query` logic.
*   Advanced result processing beyond extracting the primary content provided by the Perplexity API via LiteLLM.
*   Adding new configuration options specific to Perplexity or fine-tuning LiteLLM's retry/error handling beyond defaults. 