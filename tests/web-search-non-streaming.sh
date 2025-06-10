#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make request and extract content (with 90s timeout for web search)
response=$(curl -s --max-time 90 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kavya-m1",
    "messages": [{"role": "user", "content": "Who was president of USA in May 2025? Answer only with first name and last name and nothing else."}]
  }')

# Check if curl failed
if [ $? -ne 0 ]; then
    echo "❌ Test failed: Request timed out or failed (server likely not running)"
    exit 1
fi

# Extract status code and response body
http_status=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
response_body=$(echo "$response" | sed 's/HTTPSTATUS:[0-9]*$//')

# Check HTTP status
if [ "$http_status" != "200" ]; then
    echo "❌ Test failed: HTTP $http_status"
    echo "Response: $response_body"
    exit 1
fi

# Extract content from non-streaming response
content=$(echo "$response_body" | jq -r '.choices[0].message.content' 2>/dev/null)

# Check if jq parsing failed
if [ $? -ne 0 ] || [ -z "$content" ] || [ "$content" = "null" ]; then
    echo "❌ Test failed: Could not parse response with jq"
    echo "Full response: $response_body"
    exit 1
fi

echo "Extracted content: '$content'"

# Check if output contains "Trump" (case insensitive)
if echo "$content" | grep -qi "trump"; then
    echo "✅ Test passed: Found 'Trump' in response: '$content'"
    exit 0
elif echo "$content" | grep -qi "future\|cannot provide\|don't have access\|cutoff"; then
    echo "❌ Test failed: Web search was not triggered"
    echo "Response suggests no real-time data access: '$content'"
    exit 1
else
    echo "❌ Test failed: Expected response to contain 'Trump', got '$content'"
    echo "Full response: $response_body"
    exit 1
fi 