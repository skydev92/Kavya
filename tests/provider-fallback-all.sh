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
    "providers": "openai,anthropic,mistral,gemini,xai"
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

# Check the model type
model_name=$(echo "$response_body" | jq -r '.model' 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$model_name" ] || [ "$model_name" = "null" ]; then
    echo "❌ Test failed: Could not parse model from response"
    echo "Response: $response_body"
    exit 1
fi

if echo "$model_name" | grep -qE "(gpt|davinci|curie|babbage|ada)"; then
    echo "✅ Test passed: Got OpenAI model ($model_name) from full provider chain"
    exit 0
else
    echo "❌ Test failed: Expected OpenAI model, got $model_name"
    exit 1
fi 