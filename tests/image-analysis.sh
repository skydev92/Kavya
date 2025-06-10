#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Capture both response and status code
response=$(curl -s --max-time 60 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/chat/completions" \
  -H "Authorization: Bearer $JWT_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kavya-m1",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "What text do you see in this image? Please provide the exact text."
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHgAAAAeBAMAAAAV58emAAAAGFBMVEX///9fX1/f39+/v7+fn58/Pz9/f38fHx+hWdyxAAABR0lEQVQ4y+3OwY6CMBAG4L9TwGuFDedBxTOK8cyujWeMPkCVDQ+g759sS9nYeDBZT3vwb+cA7dcZvPPOfw/t+Pl5zSDYPV9jqDB5fXmKY31uBGOLk57ayhFEVMBzDOxJobUralzhnhI2izVoUS0q8IYx1yD5hRnA5LFgbU0LUi2kCfDWVtStOM76bI9DnaGrW/GxqgpAlR5HTVzAYdMiCSfVtgqQmmDCAlcIAN2ESQkmE3ksG3l1uOST/sYD3gBKILawhWDZHy1Q0iSMXxwdgcP0guNG4WFsPWC3FBL+4J0AlPs14qQqYtfZVRfi2D/QWuo7k0E34J32F9xOpRmxCHFk3CGNnW8oyNBtwEIhGTBllqYjTjjUn30q8zN7nPcp8rMfOzEoHZ5OOQbKEZNCmBlAGKPo/iEYf0v47BKv46XC65nhnYf8AJ/tKGkePjqCAAAAAElFTkSuQmCC"
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
    echo "✅ Test passed: Detected expected text in image ('$content')"
    exit 0
else
    echo "❌ Test failed: Expected 'DXPR' or 'company' in response, got '$content'"
    exit 1
fi 