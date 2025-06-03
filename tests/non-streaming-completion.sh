#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make request and extract content (with 60s timeout)
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

# Extract content from non-streaming response
content=$(echo "$response" | jq -r '.choices[0].message.content')

# Check if output matches expected result
if [ "$content" = "Joe Biden" ]; then
    echo "✅ Test passed: Got '$content'"
    exit 0
else
    echo "❌ Test failed: Expected 'Joe Biden', got '$content'"
    exit 1
fi 