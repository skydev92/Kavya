# Plan: Inject Web Search Results into Section Drafting Prompt

This plan outlines the steps to make web search results available within the system prompt used by the `get_content_draft` function in the Longwriter agent. It assumes Perplexity search results are concise and suitable for unconditional inclusion.

**Assumptions:**

*   The `enhance_with_web_search` function successfully prepends `<web_search_results>...</web_search_results>` to the last user message in the initial message list.
*   The `ContentStrategy` object, available within `get_content_draft`, contains these initial messages (potentially in an `original_messages` attribute).

**Steps:**

1.  **Locate Search Results within `get_content_draft` (`routellm/controller.py`):**
    *   Inside the `get_content_draft` function, add logic before defining `content_writer_prompt`.
    *   Access the initial message list (e.g., `content_strategy.original_messages`). Check if it exists and is not empty.
    *   Iterate backwards through this list to find the last message where `role == "user"`.
    *   If found, use `re.search` with `re.DOTALL` to find and extract the text content within the `<web_search_results>` tags from that user message's content.
    *   Store the extracted text in a variable (e.g., `extracted_search_results: Optional[str]`). Handle the case where the message or tags are not found (set variable to `None`).

2.  **Modify `content_writer_prompt` (`routellm/controller.py`):**
    *   Locate the definition of the `content_writer_prompt` multi-line string within `get_content_draft`.
    *   Add a new section conditionally to this prompt string. For example:
        ```python
        search_context_section = ""
        if extracted_search_results:
            search_context_section = f"""

        Context from Recent Web Search (Use this information where relevant):
        --- START SEARCH RESULTS ---
        {extracted_search_results}
        --- END SEARCH RESULTS ---
        """

        content_writer_prompt = f'''
        You are a creative content writer... [Existing prompt start] ...

        Key points:
        ... [Existing key points] ...
        {search_context_section} # Inject search context here

        Here's the outline... [Existing prompt end] ...
        '''
        ```
    *   Ensure the formatting integrates well with the existing prompt structure.

3.  **Testing:**
    *   Run a request that triggers both web search *and* the Longwriter agent.
    *   Use verbose logging. Check if the `get_content_draft` function logs indicate successful extraction of search results (add temporary logging if needed).
    *   Verify that the search results context appears within the system prompt sent to the LLM for section drafting (requires deep LiteLLM logging or inspection).
    *   Evaluate the generated section content to see if it appropriately incorporates information from the provided search results context. 