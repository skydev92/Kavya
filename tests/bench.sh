#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Get current timestamp (using underscores instead of colons for filename compatibility)
timestamp=$(date +"%Y-%m-%d_%H_%M_%S")

# Define all providers to test
providers=("anthropic" "gemini" "mistral" "openai" "xai")

# Loop through all providers
for provider in "${providers[@]}"; do
    echo "Testing provider: $provider"
    
    # Record start time
    start_time=$(date +%s)

    # Make streaming request and capture output
    output=$(curl -s --max-time 60 -X POST "http://localhost:8089/v1/chat/completions" \
      -H "Authorization: Bearer $JWT_TEST_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{
        \"model\": \"kavya-m1\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Write 200 word structured essay about chess\"}],
        \"longwriter_word_threshold\": 50,
        \"providers\": \"$provider\",
        \"stream\": true
      }" | while IFS= read -r line; do
        # Skip empty lines and non-data lines
        if [[ "$line" =~ ^data:\ (.*)$ ]]; then
            data="${BASH_REMATCH[1]}"
            # Skip [DONE] marker
            if [ "$data" != "[DONE]" ]; then
                # Extract content from JSON and output directly (no newline)
                echo "$data" | jq -r '.choices[0].delta.content // empty' | tr -d '\n'
            fi
        fi
    done)

    # Record end time and calculate duration
    end_time=$(date +%s)
    duration=$((end_time - start_time))

    # Create filename with all required components
    filename="/tmp/kavya-bench-${timestamp}-${provider}-${duration}s.txt"

    # Write output to file
    echo "$output" > "$filename"

    # Count words in output
    word_count=$(echo "$output" | wc -w | tr -d ' ')

    # Output results for this provider
    echo "$provider: Time: ${duration}s, Words: ${word_count}"
    echo ""
done

echo "All provider tests completed!"