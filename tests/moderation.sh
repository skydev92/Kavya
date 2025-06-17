#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Test 1: Safe content (should not be flagged)
echo "Testing safe content..."
response=$(curl -s --max-time 30 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/moderations" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "I would love a cup of coffee. Where can I get one nearby?"
  }')

# Check if curl failed
if [ $? -ne 0 ]; then
    echo "❌ Test failed: Request timed out or failed (server likely not running)"
    exit 1
fi

# Extract status code and response body
http_status=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
response_body=$(echo "$response" | sed 's/HTTPSTATUS:[0-9]*$//')

# Validate HTTP status and response body
if [ "$http_status" -ne 200 ]; then
    echo "❌ Test failed: Expected HTTP 200, got $http_status"
    echo "Response: $response_body"
    exit 1
fi

flagged=$(echo "$response_body" | jq -r '.results[0].flagged')
if [ "$flagged" != "false" ]; then
    echo "❌ Test failed: Safe content was flagged"
    echo "Response: $response_body"
    exit 1
fi
echo "✅ Test passed: Safe content not flagged"

# Test 2: Harmful content (should be flagged)
echo "Testing harmful content..."
response=$(curl -s --max-time 30 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/moderations" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "I am going to kill you"
  }')

http_status=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
response_body=$(echo "$response" | sed 's/HTTPSTATUS:[0-9]*$//')

if [ "$http_status" -ne 200 ]; then
    echo "❌ Test failed: Expected HTTP 200, got $http_status for harmful content"
    echo "Response: $response_body"
    exit 1
fi

flagged=$(echo "$response_body" | jq -r '.results[0].flagged')
if [ "$flagged" != "true" ]; then
    echo "❌ Test failed: Harmful content was not flagged"
    echo "Response: $response_body"
    exit 1
fi
echo "✅ Test passed: Harmful content was flagged" 