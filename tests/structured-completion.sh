#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make request with both response_format parameter AND prompt-based JSON instructions (with 60s timeout)
# This ensures compatibility with different model requirements
response=$(curl -s --max-time 60 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kavya-m1",
    "messages": [{
      "role": "user", 
      "content": "Who was president of USA in 2022? Respond with a JSON object."
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
            "year": {
              "type": "number"
            }
          },
          "required": ["first_name", "last_name", "year"],
          "additionalProperties": false
        }
      }
    }
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

# Extract content from response
content=$(echo "$response_body" | jq -r '.choices[0].message.content' 2>/dev/null)

# Check if jq parsing failed
if [ $? -ne 0 ] || [ -z "$content" ] || [ "$content" = "null" ]; then
    echo "❌ Test failed: Could not parse response with jq"
    echo "Full response: $response_body"
    exit 1
fi

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
year=$(echo "$content" | jq -r '.year')

echo "Parsed values:"
echo "  first_name: $first_name"
echo "  last_name: $last_name"
echo "  year: $year"

# Validate the structured output
if { [ "$first_name" = "Joe" ] || [ "$first_name" = "Joseph" ]; } && [ "$last_name" = "Biden" ] && [ "$year" = "2022" ]; then
    echo "✅ Test passed: Got structured JSON with correct values"
    exit 0
else
    echo "❌ Test failed: Incorrect values in structured response"
    echo "Expected: Joe/Joseph Biden, 2022"
    echo "Got: $first_name $last_name, $year"
    exit 1
fi 