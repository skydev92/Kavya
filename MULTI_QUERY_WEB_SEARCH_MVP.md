# MVP Plan: Multi-Query Web Search with Combined Analysis

This plan outlines the steps to implement dynamic multi-query web search, replacing the separate need evaluation with a combined analysis function and enabling parallel execution of searches. The existing single-query generation prompt remains unchanged for this MVP.

**Assumptions:**

*   LiteLLM is integrated and configured.
*   `PERPLEXITYAI_API_KEY` is managed via environment variables.
*   Base Pydantic models exist in `routellm/models.py`.

**Steps:**

1.  **Define Pydantic Models (`routellm/models.py`):**
    *   Create `WebSearchAnalysisResponse(BaseModel)` with fields `need_web_search: int` (0-100) and `web_search_count: int` (1-5).
    *   Create `MultipleQueryResponse(BaseModel)` with field `queries: List[str]`.

2.  **Implement Combined Analysis Function (`routellm/web_search.py`):**
    *   Create `async def analyze_web_search_request(controller, messages) -> WebSearchAnalysisResponse:`.
    *   Use weak LLM, prompt for JSON with `need_web_search` and `web_search_count`.
    *   Use `response_model=WebSearchAnalysisResponse` in `litellm.acompletion`.
    *   Include `user` ID parameter and robust error handling.

3.  **Implement Multi-Query Generation (`routellm/web_search.py`):**
    *   Create `async def generate_multiple_queries(controller, user_msg: str, count: int) -> MultipleQueryResponse:`.
    *   Use weak LLM, prompt for JSON list of `count` distinct queries.
    *   Use `response_model=MultipleQueryResponse` in `litellm.acompletion`.
    *   Include `user` ID parameter and robust error handling.

4.  **Update `search_web` Function (`routellm/web_search.py`):**
    *   Add `user: str` parameter to the function signature.
    *   Pass the `user=user` argument to the `litellm.acompletion` call inside.

5.  **Refactor `enhance_with_web_search` (Part 1 - Analysis):**
    *   Replace call to `evaluate_web_search_need` with call to `analyze_web_search_request`.
    *   Get `analysis_result` (instance of `WebSearchAnalysisResponse`).
    *   Extract `user_id` (needed for subsequent calls).
    *   Check `analysis_result.need_web_search` against the threshold from config.

6.  **Refactor `enhance_with_web_search` (Part 2 - Query Generation):**
    *   If `analysis_result.web_search_count == 1`: Call `generate_search_query(controller, user_msg)` (existing function). Store result as `search_queries: List[str] = [single_query]`.
    *   If `analysis_result.web_search_count > 1`: Call `generate_multiple_queries(controller, user_msg, analysis_result.web_search_count)`. Store result as `search_queries: List[str] = multi_query_response.queries`.

7.  **Refactor `enhance_with_web_search` (Part 3 - Concurrent Execution):**
    *   Check if `search_queries` list is not empty.
    *   Create tasks: `[search_web(query, user=user_id) for query in search_queries]`.
    *   Execute concurrently: `results_list = await asyncio.gather(*tasks)`.

8.  **Refactor `enhance_with_web_search` (Part 4 - Aggregation & Context):**
    *   Filter out `None` values from `results_list`.
    *   If results exist, join them into a single string `aggregated_results` (e.g., separated by `\n---\n`).
    *   Format context: `context = f"<web_search_results>\n{aggregated_results}\n</web_search_results>"`.
    *   Prepend context to the last user message as before.

9.  **Remove Old Function:** Delete the original `evaluate_web_search_need` function.

10. **Testing:** Test scenarios triggering both single and multiple queries, check logs for concurrency, and verify context formatting. 