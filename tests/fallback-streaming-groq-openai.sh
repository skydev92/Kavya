#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make request
response=$(curl -s --max-time 30 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kavya-m1",
    "messages": [{"role": "user", "content": "What is 2+2? Answer with just the number."}],
    "stream": true,
    "providers": "groq,openai"
  }')

# Check if curl failed
if [ $? -ne 0 ]; then
    echo "❌ Test failed: Request timed out or failed"
    exit 1
fi

# Extract status code and response body
http_status=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
response_body=$(echo "$response" | sed 's/HTTPSTATUS:[0-9]*$//')

# Check HTTP status
if [ "$http_status" != "200" ]; then
    echo "❌ Test failed: Expected HTTP 200, got $http_status"
    echo "Response: $response_body"
    exit 1
fi

# Extract model from streaming chunks (similar to streaming-completion.sh approach)
model_name=$(echo "$response_body" | grep '"model":' | head -1 | sed 's/.*"model": *"\([^"]*\)".*/\1/')
if [ -z "$model_name" ]; then
    echo "❌ Test failed: Could not extract model from streaming response"
    echo "Response: $response_body"
    exit 1
fi

# Check that we got either groq (primary) or openai (fallback) model
if echo "$model_name" | grep -qE "(groq/|o1|o3|o4|o5|gpt)"; then
    echo "✅ Test passed: Got model ($model_name) from groq-openai streaming provider chain"
    exit 0
else
    echo "❌ Test failed: Expected groq/ or openai model, got $model_name"
    exit 1
fi 