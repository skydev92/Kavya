#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make request with both response_format parameter AND prompt-based JSON instructions (with 60s timeout)
# This ensures compatibility with different model requirements
response=$(curl -s --max-time 60 -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kavya-m1",
    "messages": [{
      "role": "user", 
      "content": "Who was president of USA in 2022? Respond with a JSON object containing first_name, last_name, title, and year fields. Use this exact format: {\"first_name\": \"...\", \"last_name\": \"...\", \"title\": \"...\", \"year\": ...}"
    }],
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "president_info",
        "strict": true,
        "schema": {
          "type": "object",
          "properties": {
            "first_name": {
              "type": "string"
            },
            "last_name": {
              "type": "string"
            },
            "title": {
              "type": "string"
            },
            "year": {
              "type": "number"
            }
          },
          "required": ["first_name", "last_name", "title", "year"],
          "additionalProperties": false
        }
      }
    }
  }') || {
    echo "❌ Test failed: Request timed out or failed (server likely not running)"
    exit 1
}

# Extract content from response
content=$(echo "$response" | jq -r '.choices[0].message.content')

echo "Raw response content: $content"

# Check if content is valid JSON
if ! echo "$content" | jq . >/dev/null 2>&1; then
    echo "❌ Test failed: Response is not valid JSON"
    echo "Full response: $response"
    exit 1
fi

# Parse JSON content
first_name=$(echo "$content" | jq -r '.first_name')
last_name=$(echo "$content" | jq -r '.last_name')
title=$(echo "$content" | jq -r '.title')
year=$(echo "$content" | jq -r '.year')

echo "Parsed values:"
echo "  first_name: $first_name"
echo "  last_name: $last_name"
echo "  title: $title"
echo "  year: $year"

# Validate the structured output
if [ "$first_name" = "Joe" ] && [ "$last_name" = "Biden" ] && [ "$title" = "President" ] && [ "$year" = "2022" ]; then
    echo "✅ Test passed: Got structured JSON with correct values"
    exit 0
else
    echo "❌ Test failed: Incorrect values in structured response"
    echo "Expected: Joe Biden, President, 2022"
    echo "Got: $first_name $last_name, $title, $year"
    exit 1
fi 