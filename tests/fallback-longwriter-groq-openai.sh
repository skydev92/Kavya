#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make streaming request for longwriter content (with 5 minute timeout for long generation)
response=$(curl -s --max-time 300 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kavya-m1",
    "messages": [
      {
        "role": "system",
        "content": "You are a creative writer responding with HTML snippets to be inserted into the body field of a webpage. Do not answer with full HTML pages, navigation, etc. only the body of a blog post."
      },
      {
        "role": "user",
        "content": "Create a 100-word structured article with multiple sections about a fictional robot first encounter with music. Use proper HTML formatting with headings, subheadings, and paragraphs. This is creative fiction requiring no real-time information. Do not use web search."
      }
    ],
    "stream": true,
    "providers": "groq,openai",
    "longwriter_word_threshold": 50
  }')

# Check if curl failed
if [ $? -ne 0 ]; then
    echo "❌ Test failed: Request timed out or failed (server likely not running)"
    exit 1
fi

# Extract status code and response body
http_status=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
response_body=$(echo "$response" | sed 's/HTTPSTATUS:[0-9]*$//')

echo "HTTP Status: $http_status"
echo "Response body length: ${#response_body} characters"

# Check HTTP status
if [ "$http_status" != "200" ]; then
    echo "❌ Test failed: HTTP $http_status"
    echo "Response: $response_body"
    exit 1
fi

# Extract content from streaming chunks and concatenate
content=$(echo "$response_body" | grep '"content":' | sed 's/.*"content": *"\([^"]*\)".*/\1/' | tr -d '\n')

# Check if we got any content
if [ -z "$content" ]; then
    echo "❌ Test failed: No content extracted from streaming response"
    echo "Full response: $response_body"
    exit 1
fi

# Count words in the response (removing HTML tags for accurate count)
word_count=$(echo "$content" | sed 's/<[^>]*>//g' | wc -w | tr -d ' ')

echo "Generated content length: ${#content} characters"
echo "Word count (excluding HTML): $word_count words"

# Check if content has more than 100 words (longwriter threshold)
if [ "$word_count" -gt 100 ]; then
    echo "✅ Test passed: Longwriter generated $word_count words (>100)"
    
    # Additional check for structured content (should contain HTML elements)
    if echo "$content" | grep -q "<h[1-6]>\|<p>\|<ul>\|<ol>\|<li>"; then
        echo "✅ Additional validation: Content contains HTML structure"
    else
        echo "⚠️ Warning: Content may lack HTML structure despite system prompt"
    fi
    
    exit 0
else
    echo "❌ Test failed: Expected >100 words for longwriter, got $word_count words"
    echo "Content preview (first 200 chars): ${content:0:200}..."
    exit 1
fi 