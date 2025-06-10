#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

# Helper function to make request and check status
make_request() {
    local test_name="$1"
    local providers="$2"
    local model="$3"
    local expected_status="$4"
    local expected_provider_type="$5"  # "openai" or "anthropic" or "mistral" or "gemini" or "groq" or "xai" or "error"
    local include_providers="$6"  # "true" or "false" - whether to include providers field
    
    echo -e "${YELLOW}Testing: $test_name${NC}"
    
    # Build request data
    local request_data='{
        "model": "'$model'",
        "messages": [{"role": "user", "content": "What is 2+2? Answer with just the number."}]'
    
    # Only add providers field if explicitly requested
    if [ "$include_providers" = "true" ]; then
        request_data+=', "providers": "'$providers'"'
    fi
    
    request_data+='}'
    
    # Make request
    response=$(curl -s --max-time 30 -w "HTTPSTATUS:%{http_code}" -X POST "http://localhost:8089/v1/chat/completions" \
        -H "Authorization: Bearer $JWT_TEST_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$request_data")
    
    # Check if curl failed
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ $test_name: Request timed out or failed${NC}"
        ((TESTS_FAILED++))
        return 1
    fi
    
    # Extract status code and response body
    http_status=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
    response_body=$(echo "$response" | sed 's/HTTPSTATUS:[0-9]*$//')
    
    # Check HTTP status
    if [ "$http_status" != "$expected_status" ]; then
        echo -e "${RED}❌ $test_name: Expected HTTP $expected_status, got $http_status${NC}"
        echo "Response: $response_body"
        ((TESTS_FAILED++))
        return 1
    fi
    
    # If we expect an error, just check that we got the right status
    if [ "$expected_status" != "200" ]; then
        echo -e "${GREEN}✅ $test_name: Got expected error (HTTP $http_status)${NC}"
        ((TESTS_PASSED++))
        return 0
    fi
    
    # For successful requests, check the model type
    model_name=$(echo "$response_body" | jq -r '.model' 2>/dev/null)
    if [ $? -ne 0 ] || [ -z "$model_name" ] || [ "$model_name" = "null" ]; then
        echo -e "${RED}❌ $test_name: Could not parse model from response${NC}"
        echo "Response: $response_body"
        ((TESTS_FAILED++))
        return 1
    fi
    
    # Check if the model matches expected provider type
    case "$expected_provider_type" in
        "openai")
            if echo "$model_name" | grep -qE "(gpt|davinci|curie|babbage|ada)"; then
                echo -e "${GREEN}✅ $test_name: Got OpenAI model ($model_name)${NC}"
                ((TESTS_PASSED++))
                return 0
            else
                echo -e "${RED}❌ $test_name: Expected OpenAI model, got $model_name${NC}"
                ((TESTS_FAILED++))
                return 1
            fi
            ;;
        "anthropic")
            if echo "$model_name" | grep -qE "(claude|anthropic)"; then
                echo -e "${GREEN}✅ $test_name: Got Anthropic model ($model_name)${NC}"
                ((TESTS_PASSED++))
                return 0
            else
                echo -e "${RED}❌ $test_name: Expected Anthropic model, got $model_name${NC}"
                ((TESTS_FAILED++))
                return 1
            fi
            ;;
        "mistral")
            if echo "$model_name" | grep -qE "(mistral)"; then
                echo -e "${GREEN}✅ $test_name: Got Mistral model ($model_name)${NC}"
                ((TESTS_PASSED++))
                return 0
            else
                echo -e "${RED}❌ $test_name: Expected Mistral model, got $model_name${NC}"
                ((TESTS_FAILED++))
                return 1
            fi
            ;;
        "gemini")
            if echo "$model_name" | grep -qE "(gemini)"; then
                echo -e "${GREEN}✅ $test_name: Got Gemini model ($model_name)${NC}"
                ((TESTS_PASSED++))
                return 0
            else
                echo -e "${RED}❌ $test_name: Expected Gemini model, got $model_name${NC}"
                ((TESTS_FAILED++))
                return 1
            fi
            ;;
        "groq")
            if echo "$model_name" | grep -qE "(llama|groq)"; then
                echo -e "${GREEN}✅ $test_name: Got Groq model ($model_name)${NC}"
                ((TESTS_PASSED++))
                return 0
            else
                echo -e "${RED}❌ $test_name: Expected Groq model, got $model_name${NC}"
                ((TESTS_FAILED++))
                return 1
            fi
            ;;
        "xai")
            if echo "$model_name" | grep -qE "(grok|xai)"; then
                echo -e "${GREEN}✅ $test_name: Got xAI model ($model_name)${NC}"
                ((TESTS_PASSED++))
                return 0
            else
                echo -e "${RED}❌ $test_name: Expected xAI model, got $model_name${NC}"
                ((TESTS_FAILED++))
                return 1
            fi
            ;;
        *)
            echo -e "${GREEN}✅ $test_name: Got model $model_name${NC}"
            ((TESTS_PASSED++))
            return 0
            ;;
    esac
}

echo "🧪 Starting providers parameter tests..."
echo "========================================"

# Test 1-6: Single provider tests for all supported providers
make_request "Single provider (openai)" "openai" "kavya-m1" "200" "openai" "true"
make_request "Single provider (anthropic)" "anthropic" "kavya-m1" "200" "anthropic" "true"
make_request "Single provider (mistral)" "mistral" "kavya-m1" "200" "mistral" "true"
make_request "Single provider (gemini)" "gemini" "kavya-m1" "200" "gemini" "true"
# make_request "Single provider (groq)" "groq" "kavya-m1" "200" "groq" "true"
make_request "Single provider (xai)" "xai" "kavya-m1" "200" "xai" "true"

# Test 7-8: Multiple providers (fallback chains)
make_request "Multiple providers (openai,anthropic)" "openai,anthropic" "kavya-m1" "200" "openai" "true"
make_request "Multiple providers (anthropic,openai)" "anthropic,openai" "kavya-m1" "200" "anthropic" "true"

# Test 9: All providers (comprehensive fallback chain)
make_request "All providers chain" "openai,anthropic,mistral,gemini,xai" "kavya-m1" "200" "openai" "true"

# Test 10: Different provider combinations
make_request "Mixed providers (mistral,gemini,xai)" "mistral,gemini,xai" "kavya-m1" "200" "mistral" "true"

# Test 11: No providers parameter (should use default)
make_request "No providers parameter (default behavior)" "" "kavya-m1" "200" "any" "false"

echo ""
echo "========================================"
echo "Test Results:"
echo -e "✅ Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "❌ Failed: ${RED}$TESTS_FAILED${NC}"

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}🎉 All providers parameter tests passed!${NC}"
    exit 0
else
    echo -e "${RED}💥 Some tests failed. Check the output above for details.${NC}"
    exit 1
fi 