#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Make request with file upload (with 60s timeout)
response=$(curl -s --max-time 60 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/files" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -F "file=@small-pdf--company-dxpr.pdf" \
  -F "purpose=user_data")

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
content=$(echo "$response_body" | jq -r '.id' 2>/dev/null)

# Check if jq parsing failed
if [ $? -ne 0 ] || [ -z "$content" ] || [ "$content" = "null" ]; then
    echo "❌ Test failed: Could not parse response with jq"
    echo "Full response: $response_body"
    exit 1
fi

# Check if file was uploaded successfully
if [ -n "$content" ] && [ "$content" != "null" ]; then
    echo "✅ Test passed: File uploaded successfully with ID '$content'"
    exit 0
else
    echo "❌ Test failed: No file ID returned"
    exit 1
fi 