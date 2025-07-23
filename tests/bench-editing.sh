#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Get current timestamp (using underscores instead of colons for filename compatibility)
timestamp=$(date +"%Y-%m-%d_%H_%M_%S")

# Define all providers to test
providers=("openai" "xai" "mistral" "gemini" "anthropic")

# Existing HTML content to modify
existing_html='<section class="text-white bg-primary hero py-5"><div class=container><div class="align-items-center min-vh-100 row"><div class=col-lg-6><h1 class="fw-bold display-4 mb-4">Welcome to Our Amazing Platform</h1><p class="lead mb-4">Discover the power of innovation with our cutting-edge solutions. We help businesses transform their digital presence and achieve remarkable growth in today'"'"'s competitive landscape.</p></div><div class=col-lg-6><div class=text-center><img alt="Hero Image"class="img-fluid rounded shadow-lg"src="https://via.placeholder.com/600x400/ffffff/000000?text=Hero+Image"></div></div></div></div></section><section class="py-5 features"><div class=container><div class="text-center row"><div class="col-12 mb-5"><h2 class="fw-bold display-5">Why Choose Us?</h2><p class="text-muted lead">Three compelling reasons to get started</div></div><div class="row g-4"><div class=col-md-4><div class="border-0 card h-100 shadow-sm"><div class="text-center card-body p-4"><div class="align-items-center d-inline-flex justify-content-center mb-3 rounded-circle text-white bg-primary"style=width:60px;height:60px><i class="fas fs-4 fa-rocket"></i></div><h5 class="fw-bold card-title">Fast Performance</h5><p class="text-muted card-text">Lightning-fast loading times and optimized performance for the best user experience.</div></div></div><div class=col-md-4><div class="border-0 card h-100 shadow-sm"><div class="text-center card-body p-4"><div class="align-items-center d-inline-flex justify-content-center mb-3 rounded-circle text-white bg-success"style=width:60px;height:60px><i class="fas fs-4 fa-shield-alt"></i></div><h5 class="fw-bold card-title">Secure & Reliable</h5><p class="text-muted card-text">Enterprise-grade security with 99.9% uptime guarantee to keep your data safe.</div></div></div><div class=col-md-4><div class="border-0 card h-100 shadow-sm"><div class="text-center card-body p-4"><div class="align-items-center d-inline-flex justify-content-center mb-3 rounded-circle text-white bg-info"style=width:60px;height:60px><i class="fas fs-4 fa-users"></i></div><h5 class="fw-bold card-title">24/7 Support</h5><p class="text-muted card-text">Round-the-clock customer support from our dedicated team of experts.</div></div></div></div><div class="text-center mt-4"><div class="d-flex flex-wrap gap-3 justify-content-center"><a class="btn btn-lg px-4 btn-primary"href=#>Get Started Today</a> <a class="btn btn-lg px-4 btn-outline-primary"href=#>Learn More</a></div></div></div></section>'

# Loop through all providers
for provider in "${providers[@]}"; do
    echo "Testing provider: $provider"
    
    # Record start time
    start_time=$(date +%s)

    # Use jq to properly construct the JSON payload to avoid escaping issues
    json_payload=$(jq -n \
      --arg model "kavya-m1" \
      --arg content "Please modify the following HTML code by changing only the button text to something more creative and engaging, but keep everything else exactly the same: $existing_html" \
      --arg provider "$provider" \
      --arg prediction_content "$existing_html" \
      '{
        model: $model,
        messages: [{role: "user", content: $content}],
        providers: $provider,
        stream: true,
        prediction: {
          type: "content",
          content: $prediction_content
        }
      }')
    
    # Make streaming request and capture raw response - asking to change button text only
    raw_response=$(curl -s --max-time 300 -X POST "http://localhost:8089/v1/chat/completions" \
      -H "Authorization: Bearer $JWT_TEST_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$json_payload")

    # Process the raw response to extract content
    output=$(echo "$raw_response" | while IFS= read -r line; do
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
    filename="/tmp/kavya-bench-button-text-${timestamp}-${provider}-${duration}s.html"

    # Write output to file
    echo "$output" > "$filename"

    # Count words in output
    word_count=$(echo "$output" | wc -w | tr -d ' ')

    # Output results for this provider
    echo "$provider: Time: ${duration}s, Words: ${word_count}"
    if [ "$word_count" -eq 0 ]; then
        echo "Full response for failed request:"
        echo "$raw_response"
    fi
    echo ""
done

echo "All provider tests completed!"
