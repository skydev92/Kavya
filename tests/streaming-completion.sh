#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make request and extract content (with 60s timeout)
response=$(curl -s --max-time 60 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kavya-m1",
    "messages": [{"role": "user", "content": "Who was president of USA in 2022? Answer only with first name and last name and nothing else."}],
    "stream": true
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

# Extract content from streaming chunks and concatenate
content=$(echo "$response_body" | grep '"content":' | sed 's/.*"content": *"\([^"]*\)".*/\1/' | tr -d '\n')

# Check if response could be processed
if [ -z "$content" ]; then
    echo "❌ Test failed: Could not extract content from response"
    echo "Full response: $response_body"
    exit 1
fi

# Check if output matches expected result
if [ "$content" = "Joe Biden" ]; then
    echo "✅ Test passed: Got '$content'"
    exit 0
else
    echo "❌ Test failed: Expected 'Joe Biden', got '$content'"
    exit 1
fi 