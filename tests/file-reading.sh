#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make request with file reading (with 60s timeout)
echo "Making request to http://localhost:8089/v1/chat/completions..."

# Capture both response and status code
response=$(curl -s --max-time 60 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "What text do you see in this PDF file? Please provide the exact text."
          },
          {
            "type": "file",
            "file": {
              "file_id": "file-Ump5JRQNkNcZPkMwS1fpWv"
            }
          }
        ]
      }
    ]
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
    
    # Check if it's a file not found error
    if echo "$response_body" | grep -q "were not found"; then
        echo ""
        echo "📝 NOTE: The file ID was not found on the server."
        echo "Please run the file upload test first:"
        echo "  ./tests/file-upload.sh"
        echo "Then copy the returned file ID and update this test file with the correct file_id."
    fi
    exit 1
fi

# Extract content from response (using response_body instead of response)
content=$(echo "$response_body" | jq -r '.choices[0].message.content' 2>/dev/null)

# Check if jq parsing failed
if [ $? -ne 0 ] || [ -z "$content" ] || [ "$content" = "null" ]; then
    echo "❌ Test failed: Could not parse response with jq"
    echo "Full response: $response_body"
    exit 1
fi

# Check if the response contains "DXPR" or "Company"
if echo "$content" | grep -i "DXPR" > /dev/null || echo "$content" | grep -i "company" > /dev/null; then
    echo "✅ Test passed: Detected expected text in PDF file ('$content')"
    exit 0
else
    echo "❌ Test failed: Expected 'DXPR' or 'company' in response, got '$content'"
    exit 1
fi 