import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional

import litellm
import yaml

from kavya.constants import AgentType, ObservationName
from kavya.langfuse_helpers import create_langfuse_metadata, observation_manager
from kavya.models import MultipleQueryResponse, SourceItem, WebSearchAnalysisResponse


def get_web_search_config():
    """Get web search configuration from config.yaml. Requires explicit threshold setting."""
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)

        if "web_search" not in config:
            raise ValueError(
                "web_search configuration section is missing from config.yaml"
            )

        web_search_config = config["web_search"]
        if "need_threshold" not in web_search_config:
            raise ValueError("need_threshold is required in web_search configuration")

        return web_search_config

    except Exception as e:
        logging.error(
            f"WEB_SEARCH: Failed to load required web search configuration: {str(e)}"
        )
        raise RuntimeError(f"Web search configuration error: {str(e)}")


async def analyze_web_search_request(
    controller, messages
) -> Optional[WebSearchAnalysisResponse]:
    """Analyze user request for web search need and optimal query count."""
    last_user_message = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    if not last_user_message:
        logging.warning("WEB_SEARCH: Could not extract last user message for analysis.")
        return None

    # Get user ID for tracking
    user_id = next(
        (m.get("user") for m in messages if isinstance(m, dict) and "user" in m), None
    )
    if not user_id and hasattr(controller, "user"):
        user_id = controller.user
    user_id = user_id or "system_web_search_analyzer"

    # Get current date for prompt context
    current_date = datetime.now().strftime("%Y-%m-%d")

    # Prompt for combined analysis
    prompt = f"""Today's date is {current_date}.
Analyze the following user request. Determine the need for real-time web search and the optimal number of distinct search queries required.

Request: "{last_user_message}"

Respond ONLY with a JSON object containing two fields:
- "need_web_search": An integer score (0-100) indicating how much web search is needed (0=none, 100=essential).
- "web_search_count": An integer (1-5) representing the optimal number of distinct search queries needed to comprehensively answer the request if search is required. If need_web_search is low (e.g., < 30), web_search_count should typically be 1. Consider complexity, multiple topics, comparisons etc.

Examples:
- Request: "What is 2+2?" -> {{"need_web_search": 0, "web_search_count": 1}}
- Request: "Latest AI developments?" -> {{"need_web_search": 90, "web_search_count": 1}}
- Request: "Compare speed and cost of Model A vs Model B." -> {{"need_web_search": 75, "web_search_count": 2}}
- Request: "Summarize the plot, themes, and reception of book X." -> {{"need_web_search": 60, "web_search_count": 3}}
"""

    try:
        # Use response_format=json_object instead of response_model
        raw_response = await litellm.acompletion(
            model=controller.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},  # Ask for JSON string
            temperature=0.1,
            user=user_id,
            metadata=create_langfuse_metadata(
                agent_type=AgentType.WEB_SEARCH,
                step="analyze_search_need",
                generation_name="web_search_need_analyzer",
                user_id=user_id,
                trace_name="web_search_workflow",
                additional_metadata={
                    "originating_controller": controller.__class__.__name__,
                },
                tags=[
                    f"model:{controller.model}",
                ],
            ),
        )

        # Manually parse and validate the response
        if raw_response and raw_response.choices:
            content = raw_response.choices[0].message.content.strip()
            # Clean potential markdown formatting
            content = re.sub(
                r"^```json\s*|\s*```$", "", content, flags=re.MULTILINE
            ).strip()
            try:
                data = json.loads(content)
                # Validate with Pydantic model
                analysis = WebSearchAnalysisResponse(**data)
                return analysis
            except (
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ) as parse_error:  # Broader catch for validation errors
                logging.error(
                    f"WEB_SEARCH Analysis: Failed to parse/validate JSON: {parse_error}. Content: {content}",
                    exc_info=True,
                )
                return None
        else:
            logging.error(
                f"WEB_SEARCH Analysis: Received unexpected response structure from LiteLLM: {raw_response}"
            )
            return None

    except Exception as e:
        logging.error(
            f"WEB_SEARCH Analysis: LiteLLM call failed - {str(e)}", exc_info=True
        )
        return None


async def generate_multiple_queries(
    controller, user_msg: str, count: int, user_id: str
) -> Optional[MultipleQueryResponse]:
    """Generate a specified number of distinct search queries using the LLM."""

    # Get current year for time-sensitive queries
    current_year = datetime.now().year

    # Revised prompt emphasizing deconstruction and time-sensitivity
    prompt = f"""**Deconstruct** the following user request into its core components or distinct sub-topics. Generate exactly {count} specific, search-engine-friendly queries where **each query targets ONLY ONE** of these distinct components or sub-topics.

Request: "{user_msg}"

Guidelines for Queries:
- Each query MUST focus on a single, unique aspect of the original request.
- Ensure the queries are substantially different from each other.
- Use specific keywords relevant to the sub-topic.
- Avoid conversational filler or action verbs like "write", "explain", "list".
- If the request involves comparisons (e.g., A vs B on criteria X, Y), generate separate queries for each comparison point or criterion as needed to meet the {count}.
- **IMPORTANT Time Sensitivity:**
  - For current events, news, technology, or people in ongoing roles, ADD "{current_year}" to the relevant query.
  - If the user mentions "current", "latest", "recent", or "now", INCLUDE the year {current_year} in the relevant query.
  - For rapidly evolving fields (tech, science, politics, entertainment), ADD the year {current_year} to the relevant query.
  - Only omit the year for timeless topics (math concepts, historical events before {current_year-2}, fundamental science).

Return ONLY a JSON object containing a single key "queries" which is a list of exactly {count} strings.

Examples:
- Request: "Compare speed and cost of Model A vs Model B.", count=2 -> {{"queries": ["Model A vs Model B speed comparison", "Model A vs Model B cost comparison"]}}
- Request: "Summarize the plot, themes, and reception of book X.", count=3 -> {{"queries": ["Book X plot summary", "Book X main themes analysis", "Book X critical reception review"]}}
- Request: "Latest Pixel vs iPhone battery and camera?", count=4 -> {{"queries": [f"latest Google Pixel phone battery life {current_year}", f"latest iPhone battery life {current_year}", f"latest Google Pixel phone camera performance {current_year}", f"latest iPhone camera performance {current_year}"]}} # (Example of finer-grained split if count allows)
"""

    try:
        # Use response_format=json_object instead of response_model
        raw_response = await litellm.acompletion(
            model=controller.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},  # Ask for JSON string
            temperature=0.2,
            user=user_id,
            metadata=create_langfuse_metadata(
                agent_type=AgentType.WEB_SEARCH,
                step="generate_search_queries",
                generation_name="web_search_query_generator",
                user_id=user_id,
                parent_observation_id=observation_manager.get_observation_id(
                    ObservationName.WEB_SEARCH
                ),
                additional_metadata={
                    "search_queries_to_generate": count,
                    "originating_controller": controller.__class__.__name__,
                },
                tags=[
                    f"query_count:{count}",
                    f"model:{controller.model}",
                ],
            ),
        )

        # Manually parse and validate the response
        if raw_response and raw_response.choices:
            content = raw_response.choices[0].message.content.strip()
            # Clean potential markdown formatting
            content = re.sub(
                r"^```json\s*|\s*```$", "", content, flags=re.MULTILINE
            ).strip()
            try:
                data = json.loads(content)
                # Validate with Pydantic model
                multi_query_resp = MultipleQueryResponse(**data)
                # Extra check: ensure the correct number of queries was returned
                if len(multi_query_resp.queries) == count:
                    return multi_query_resp
                else:
                    logging.warning(
                        f"Multi-Query Gen: LLM returned {len(multi_query_resp.queries)} queries, expected {count}. Data: {data}"
                    )
                    return None  # Or potentially try to use the queries anyway?
            except (json.JSONDecodeError, TypeError, ValueError) as parse_error:
                logging.error(
                    f"Multi-Query Gen: Failed to parse/validate JSON: {parse_error}. Content: {content}",
                    exc_info=True,
                )
                return None
        else:
            logging.error(
                f"Multi-Query Gen: Received unexpected response structure from LiteLLM: {raw_response}"
            )
            return None

    except Exception as e:
        logging.error(f"Multi-Query Gen: LiteLLM call failed - {str(e)}", exc_info=True)
        return None


async def enhance_with_web_search(controller, messages):
    """Add web search results to messages if web search need is above threshold.
    May perform multiple searches concurrently for complex requests."""
    # Create a parent observation for the entire web search workflow
    observation_manager.create_observation_id(ObservationName.WEB_SEARCH)

    # Check if the Perplexity API key is set for LiteLLM to use
    perplexity_api_key_present = os.environ.get("PERPLEXITYAI_API_KEY") is not None
    if not perplexity_api_key_present:
        observation_manager.clear_observation(ObservationName.WEB_SEARCH)
        return messages, []  # Skip if key is missing

    try:
        # --- Part 1: Analysis ---
        analysis_result = await analyze_web_search_request(controller, messages)
        if not analysis_result:
            logging.warning(
                "WEB_SEARCH: Failed to get analysis result. Skipping web search."
            )
            observation_manager.clear_observation(ObservationName.WEB_SEARCH)
            return messages, []

        # Get threshold from config
        config = get_web_search_config()
        threshold = config["need_threshold"]

        # Check if search is needed based on score
        if analysis_result.need_web_search <= threshold:
            logging.info(
                f"WEB_SEARCH: Need score {analysis_result.need_web_search} <= threshold {threshold}. Skipping search."
            )
            observation_manager.clear_observation(ObservationName.WEB_SEARCH)
            return messages, []

        logging.info(
            f"WEB_SEARCH: Need score {analysis_result.need_web_search} > threshold {threshold}. Proceeding with {analysis_result.web_search_count} search(es)."
        )

        # Extract user query for search generation
        user_msg = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        if not user_msg:
            logging.error(
                "WEB_SEARCH: Could not extract user message for query generation."
            )
            observation_manager.clear_observation(ObservationName.WEB_SEARCH)
            return messages, []  # Cannot proceed without user message

        # Extract user ID (do this once, use for all subsequent calls)
        user_id = next(
            (m.get("user") for m in messages if isinstance(m, dict) and "user" in m),
            None,
        )
        if not user_id and hasattr(controller, "user"):
            user_id = controller.user
        user_id = (
            user_id or "system_web_search_process"
        )  # Fallback user for the process

        # --- Part 2: Query Generation ---
        search_queries: List[str] = []
        if analysis_result.web_search_count <= 1:
            logging.info("WEB_SEARCH: Generating 1 search query.")
            single_query = await generate_search_query(controller, user_msg)
            if single_query:
                search_queries = [single_query]
                logging.info(f"WEB_SEARCH: Generated single query: '{single_query}'")
            else:
                logging.warning("WEB_SEARCH: Failed to generate single search query.")
        else:
            logging.info(
                f"WEB_SEARCH: Generating {analysis_result.web_search_count} search queries."
            )
            multi_query_response = await generate_multiple_queries(
                controller, user_msg, analysis_result.web_search_count, user_id
            )
            if multi_query_response and multi_query_response.queries:
                search_queries = multi_query_response.queries
                logging.info(
                    f"WEB_SEARCH: Generated multiple queries: {search_queries}"
                )
            else:
                logging.warning(
                    f"WEB_SEARCH: Failed to generate {analysis_result.web_search_count} search queries."
                )

        if not search_queries:
            logging.warning(
                "WEB_SEARCH: No search queries were generated. Skipping search execution."
            )
            return messages, []

        # --- Part 3: Concurrent Execution ---
        logging.info(
            f"PERPLEXITY_SEARCH: Executing {len(search_queries)} search(es) concurrently."
        )
        tasks = [search_web(query, user=user_id) for query in search_queries]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        # --- Part 4: Aggregation & Context ---
        successful_results = [
            res
            for res in results_list
            if res is not None and not isinstance(res, Exception)
        ]
        exceptions = [res for res in results_list if isinstance(res, Exception)]

        if exceptions:
            logging.warning(
                f"WEB_SEARCH: Encountered {len(exceptions)} error(s) during concurrent search: {[str(e) for e in exceptions]}"
            )

        if not successful_results:
            logging.warning("WEB_SEARCH: No successful search results obtained.")
            return messages, []

        # Aggregate results (simple concatenation with separator)
        aggregated_results = "\n\n---\n\n".join(
            [result[0] for result in successful_results]
        )
        titles_urls = [result[1] for result in successful_results]
        logging.debug(
            f"WEB_SEARCH: Search results: {[result[1] for result in successful_results]}"
        )

        # Add results in a simple web_search_results tag
        context = f"<web_search_results>\n{aggregated_results}\n</web_search_results>"

        # Log the beginning of the context
        logging.info(
            f"WEB_SEARCH: Adding combined search context ({len(successful_results)} results, first 20000 chars):\n{context[:20000]}"
        )

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
            new_msgs[last_user_msg_index][
                "content"
            ] = f"{context}\n\n{original_content}"
            logging.info(
                f"WEB_SEARCH: Appended combined search context to last user message at index {last_user_msg_index}"
            )
            # Clean up the web search observation when workflow completes successfully
            observation_manager.clear_observation(ObservationName.WEB_SEARCH)
            return new_msgs, titles_urls
        else:
            # Hard fail if no user message found - don't use fallback
            logging.error(
                "WEB_SEARCH: No user message found to append search context to. Cannot proceed."
            )
            observation_manager.clear_observation(ObservationName.WEB_SEARCH)
            raise RuntimeError(
                "No user message found in conversation history. Web search results cannot be attached."
            )

    except Exception as e:
        logging.error(
            f"WEB_SEARCH: Error during multi-query search enhancement: {str(e)}",
            exc_info=True,
        )
        # Clean up observation on error
        observation_manager.clear_observation(ObservationName.WEB_SEARCH)
        return messages, []


async def generate_search_query(controller, user_msg: str) -> str:
    """Generate an optimized search query using the LLM."""
    # Extract text between <TASK> tags if present
    task_match = re.search(r"<TASK>(.*?)</TASK>", user_msg, re.DOTALL)
    if task_match:
        user_msg = task_match.group(1).strip()

    # Remove any HTML tags and special markers
    user_msg = re.sub(r"<[^>]+>", "", user_msg)
    user_msg = re.sub(r"@@@\w+@@@", "", user_msg)
    user_msg = re.sub(r"\s+", " ", user_msg).strip()

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
    user_id = getattr(controller, "user", "system_query_generator")

    try:
        # Use the model to generate the query
        response = await litellm.acompletion(
            model=controller.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            user=user_id,
            metadata=create_langfuse_metadata(
                agent_type=AgentType.WEB_SEARCH,
                step="generate_single_query",
                generation_name="web_search_single_query",
                user_id=user_id,
                parent_observation_id=observation_manager.get_observation_id(
                    ObservationName.WEB_SEARCH
                ),
                additional_metadata={
                    "originating_controller": controller.__class__.__name__,
                },
                tags=[
                    f"model:{controller.model}",
                ],
            ),
        )

        # Extract and clean the generated query
        generated_query = response["choices"][0]["message"]["content"].strip()

        # Use original user message as fallback if generation fails
        if not generated_query:
            logging.warning("WEB_SEARCH: Empty query generated, using original message")
            return user_msg[:150]  # Limit to 150 chars just in case

        # Remove any quotes around the entire query if present
        if (generated_query.startswith('"') and generated_query.endswith('"')) or (
            generated_query.startswith("'") and generated_query.endswith("'")
        ):
            generated_query = generated_query[1:-1]

        # Add current year if it's not already in the query and seems like a current topic
        if str(current_year) not in generated_query:
            lower_query = generated_query.lower()
            time_indicators = [
                "current",
                "latest",
                "recent",
                "now",
                "today",
                "this year",
            ]
            topic_indicators = [
                "news",
                "update",
                "trend",
                "development",
                "release",
                "version",
                "state of",
            ]

            # Check if query contains time indicators but no year
            if any(
                indicator in lower_query
                for indicator in time_indicators + topic_indicators
            ):
                generated_query = f"{generated_query} {current_year}"
                logging.info(f"WEB_SEARCH: Added year to query: '{generated_query}'")

        return generated_query

    except Exception as e:
        logging.error(f"WEB_SEARCH: Query generation failed: {str(e)}")
        return user_msg[:150]  # Fallback to truncated original message


async def search_web(query: str, user: str):
    """Search the web using Perplexity AI API via LiteLLM and return text response."""
    logging.info(
        f"PERPLEXITY_SEARCH: Searching for: '{query}' via LiteLLM for user: {user}"
    )

    model_name = "perplexity/sonar-pro"  # Using sonar-pro as requested
    messages = [
        # Optional: Add system prompt if desired/recommended by Perplexity for search
        # {"role": "system", "content": "Provide concise and factual search results."},
        {"role": "user", "content": query}
    ]
    pydantic_search_results = []

    try:
        # LiteLLM handles retries based on its configuration.
        # Add other relevant LiteLLM parameters if needed (e.g., temperature=0.3)
        response = await litellm.acompletion(
            model=model_name,
            messages=messages,
            user=user,
            metadata=create_langfuse_metadata(
                agent_type=AgentType.WEB_SEARCH,
                step="execute_search",
                generation_name="perplexity_web_search",
                user_id=user,
                parent_observation_id=observation_manager.get_observation_id(
                    ObservationName.WEB_SEARCH
                ),
                additional_metadata={
                    "query_text": query,
                    "search_engine_provider": "perplexity",
                },
                tags=[
                    "provider:perplexity",
                    f"model:{model_name}",
                ],
            ),
        )

        # Extract result (check LiteLLM response structure - assuming standard format)
        if (
            response
            and response.get("choices")
            and response["choices"][0].get("message")
        ):
            result_text = response["choices"][0]["message"]["content"].strip()
            logging.debug(
                "PERPLEXITY_SEARCH: Search results " + str(response["search_results"])
            )
            search_results = response["search_results"]

            for result in search_results:
                if "date" in result:
                    del result["date"]
                pydantic_search_results.append(SourceItem(**result))
        else:
            logging.warning(
                f"PERPLEXITY_SEARCH: Received unexpected response structure from LiteLLM: {response}"
            )
            return None

        # Limit text length if necessary
        if len(result_text) > 200000:
            result_text = result_text[:200000]
            logging.info(
                "PERPLEXITY_SEARCH: Truncated response text to 200000 characters"
            )

        logging.info(
            f"PERPLEXITY_SEARCH: Successfully retrieved search results ({len(result_text)} chars)"
        )
        return result_text, pydantic_search_results

    except Exception as e:
        # Handle potential exceptions from LiteLLM
        # Consider specific exception types based on LiteLLM docs if available
        # (e.g., litellm.exceptions.AuthenticationError, litellm.exceptions.RateLimitError)
        logging.error(
            f"PERPLEXITY_SEARCH: LiteLLM call failed for user {user}: {str(e)}",
            exc_info=True,
        )
        # Return exception instead of None to be caught by asyncio.gather
        raise e  # Re-raise exception to be caught by gather
