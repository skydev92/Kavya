#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

echo "🔍 Debug: Testing API call..."
echo "🔍 Debug: JWT_TEST_TOKEN is set: ${JWT_TEST_TOKEN:+YES}${JWT_TEST_TOKEN:-NO}"

# Test server health first
echo "🔍 Debug: Testing server health..."
health_response=$(curl -s --max-time 10 http://localhost:8089/health 2>/dev/null || echo "NO_HEALTH_ENDPOINT")
echo "🔍 Debug: Health response: $health_response"

# Test models endpoint if available
echo "🔍 Debug: Testing models endpoint..."
models_response=$(curl -s --max-time 10 -H "Authorization: Bearer $JWT_TEST_TOKEN" http://localhost:8089/v1/models 2>/dev/null || echo "NO_MODELS_ENDPOINT")
echo "🔍 Debug: Models response: $models_response"

# Make request and extract content (with 60s timeout)
echo "🔍 Debug: Making chat completion request..."
response=$(curl -s --max-time 60 -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kavya-m1",
    "messages": [{"role": "user", "content": "Who was president of USA in 2022? Answer only with first name and last name and nothing else."}]
  }') || {
    echo "❌ Test failed: Request timed out or failed (server likely not running)"
    exit 1
}

echo "🔍 Debug: Full API response:"
echo "$response" | jq .

# Check if we got an error response
error_message=$(echo "$response" | jq -r '.error.message // empty')
if [ -n "$error_message" ]; then
    echo "❌ Test failed: API returned error: $error_message"
    exit 1
fi

# Extract content from non-streaming response
content=$(echo "$response" | jq -r '.choices[0].message.content // empty')

echo "🔍 Debug: Extracted content: '$content'"

# Check if output matches expected result
if [ "$content" = "Joe Biden" ]; then
    echo "✅ Test passed: Got '$content'"
    exit 0
else
    echo "❌ Test failed: Expected 'Joe Biden', got '$content'"
    echo "🔍 Debug: Response structure check:"
    echo "  - choices array exists: $(echo "$response" | jq 'has("choices")')"
    echo "  - choices[0] exists: $(echo "$response" | jq '.choices[0] // "missing"')"
    echo "  - message exists: $(echo "$response" | jq '.choices[0].message // "missing"')"
    echo "  - content exists: $(echo "$response" | jq '.choices[0].message.content // "missing"')"
    exit 1
fi 