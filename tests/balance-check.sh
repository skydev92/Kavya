#!/bin/bash

set -e

# Load JWT token from .env
export $(grep -v '^#' .env | xargs)

# Function to get balance
get_balance() {
    local response=$(curl -s --max-time 30 -w "HTTPSTATUS:%{http_code}" -X GET "http://localhost:8089/v1/account/balance" \
      -H "Authorization: Bearer $JWT_TEST_TOKEN" \
      -H "Content-Type: application/json")
    
    # Check if curl failed
    if [ $? -ne 0 ]; then
        echo "❌ Balance check failed: Request timed out or failed"
        exit 1
    fi
    
    # Extract status code and response body
    local http_status=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
    local response_body=$(echo "$response" | sed 's/HTTPSTATUS:[0-9]*$//')
    
    # Check HTTP status
    if [ "$http_status" != "200" ]; then
        echo "❌ Balance check failed: HTTP $http_status"
        echo "Response: $response_body"
        exit 1
    fi
    
    echo "$response_body"
}

# Parse JSON values (simple extraction without jq dependency)
parse_json_value() {
    local json="$1"
    local key="$2"
    echo "$json" | grep -o "\"$key\":\"[^\"]*\"" | cut -d'"' -f4
}

# Parse nested JSON values 
parse_nested_json_value() {
    local json="$1"
    local parent_key="$2"
    local child_key="$3"
    # Extract the parent object first, then the child value
    local parent_obj=$(echo "$json" | grep -o "\"$parent_key\":{[^}]*}" | sed "s/\"$parent_key\"://")
    echo "$parent_obj" | grep -o "\"$child_key\":[^,}]*" | cut -d: -f2 | tr -d '"' | tr -d ' '
}

case "$1" in
    "before")
        echo "🔍 Checking balance before tests..."
        balance_response=$(get_balance)
        echo "Balance response: $balance_response"
        
        # Store balance values in temporary files
        parse_nested_json_value "$balance_response" "balance" "token_balance_in" > /tmp/token_balance_in_before
        parse_nested_json_value "$balance_response" "balance" "token_balance_out" > /tmp/token_balance_out_before
        parse_nested_json_value "$balance_response" "monthly_usage" "prompt_tokens" > /tmp/prompt_tokens_before
        parse_nested_json_value "$balance_response" "monthly_usage" "completion_tokens" > /tmp/completion_tokens_before
        parse_nested_json_value "$balance_response" "monthly_usage" "transaction_count" > /tmp/transaction_count_before
        
        # Validate required fields exist in response
        echo "🔍 Validating response structure..."
        required_fields=("account_id" "last_updated")
        balance_fields=("token_balance_in" "token_balance_out" "word_balance" "transactions" "total_token_usage_in" "total_token_usage_out" "total_word_usage")
        
        # Check top-level fields
        for field in "${required_fields[@]}"; do
            if ! echo "$balance_response" | grep -q "\"$field\""; then
                echo "❌ Missing required field: $field"
                exit 1
            fi
        done
        
        # Check balance object fields
        for field in "${balance_fields[@]}"; do
            if ! echo "$balance_response" | grep -q "\"$field\""; then
                echo "❌ Missing balance field: $field"
                exit 1
            fi
        done
        
        echo "✅ All required fields present in response"
        
        # Store additional balance values
        parse_nested_json_value "$balance_response" "balance" "word_balance" > /tmp/word_balance_before
        parse_nested_json_value "$balance_response" "balance" "total_token_usage_in" > /tmp/total_token_usage_in_before
        parse_nested_json_value "$balance_response" "balance" "total_token_usage_out" > /tmp/total_token_usage_out_before
        parse_nested_json_value "$balance_response" "balance" "total_word_usage" > /tmp/total_word_usage_before
        parse_json_value "$balance_response" "last_updated" > /tmp/last_updated_before
        
        echo "✅ Balance check before tests completed"
        echo "Initial token_balance_in: $(cat /tmp/token_balance_in_before)"
        echo "Initial token_balance_out: $(cat /tmp/token_balance_out_before)"
        echo "Initial word_balance: $(cat /tmp/word_balance_before)"
        echo "Initial total_token_usage_in: $(cat /tmp/total_token_usage_in_before)"
        echo "Initial total_token_usage_out: $(cat /tmp/total_token_usage_out_before)"
        echo "Initial total_word_usage: $(cat /tmp/total_word_usage_before)"
        echo "Initial prompt_tokens: $(cat /tmp/prompt_tokens_before)"
        echo "Initial completion_tokens: $(cat /tmp/completion_tokens_before)"
        echo "Initial transaction_count: $(cat /tmp/transaction_count_before)"
        echo "Initial last_updated: $(cat /tmp/last_updated_before)"
        ;;
        
    "after")
        echo "🔍 Checking balance after tests..."
        balance_response=$(get_balance)
        echo "Balance response: $balance_response"
        
        # Validate required fields exist in response
        echo "🔍 Validating response structure..."
        required_fields=("account_id" "last_updated")
        balance_fields=("token_balance_in" "token_balance_out" "word_balance" "transactions" "total_token_usage_in" "total_token_usage_out" "total_word_usage")
        
        # Check top-level fields
        for field in "${required_fields[@]}"; do
            if ! echo "$balance_response" | grep -q "\"$field\""; then
                echo "❌ Missing required field: $field"
                exit 1
            fi
        done
        
        # Check balance object fields
        for field in "${balance_fields[@]}"; do
            if ! echo "$balance_response" | grep -q "\"$field\""; then
                echo "❌ Missing balance field: $field"
                exit 1
            fi
        done
        
        echo "✅ All required fields present in response"
        
        # Get current balance values
        current_token_balance_in=$(parse_nested_json_value "$balance_response" "balance" "token_balance_in")
        current_token_balance_out=$(parse_nested_json_value "$balance_response" "balance" "token_balance_out")
        current_word_balance=$(parse_nested_json_value "$balance_response" "balance" "word_balance")
        current_total_token_usage_in=$(parse_nested_json_value "$balance_response" "balance" "total_token_usage_in")
        current_total_token_usage_out=$(parse_nested_json_value "$balance_response" "balance" "total_token_usage_out")
        current_total_word_usage=$(parse_nested_json_value "$balance_response" "balance" "total_word_usage")
        current_prompt_tokens=$(parse_nested_json_value "$balance_response" "monthly_usage" "prompt_tokens")
        current_completion_tokens=$(parse_nested_json_value "$balance_response" "monthly_usage" "completion_tokens")
        current_transaction_count=$(parse_nested_json_value "$balance_response" "monthly_usage" "transaction_count")
        current_last_updated=$(parse_json_value "$balance_response" "last_updated")
        
        # Read initial values
        initial_token_balance_in=$(cat /tmp/token_balance_in_before 2>/dev/null || echo "0")
        initial_token_balance_out=$(cat /tmp/token_balance_out_before 2>/dev/null || echo "0")
        initial_prompt_tokens=$(cat /tmp/prompt_tokens_before 2>/dev/null || echo "0")
        initial_completion_tokens=$(cat /tmp/completion_tokens_before 2>/dev/null || echo "0")
        initial_transaction_count=$(cat /tmp/transaction_count_before 2>/dev/null || echo "0")
        
        # Read additional initial values
        initial_word_balance=$(cat /tmp/word_balance_before 2>/dev/null || echo "0")
        initial_total_token_usage_in=$(cat /tmp/total_token_usage_in_before 2>/dev/null || echo "0")
        initial_total_token_usage_out=$(cat /tmp/total_token_usage_out_before 2>/dev/null || echo "0")
        initial_total_word_usage=$(cat /tmp/total_word_usage_before 2>/dev/null || echo "0")
        initial_last_updated=$(cat /tmp/last_updated_before 2>/dev/null || echo "")
        
        echo "Comparison:"
        echo "Token Balance In: $initial_token_balance_in → $current_token_balance_in"
        echo "Token Balance Out: $initial_token_balance_out → $current_token_balance_out"
        echo "Word Balance: $initial_word_balance → $current_word_balance"
        echo "Total Token Usage In: $initial_total_token_usage_in → $current_total_token_usage_in"
        echo "Total Token Usage Out: $initial_total_token_usage_out → $current_total_token_usage_out"
        echo "Total Word Usage: $initial_total_word_usage → $current_total_word_usage"
        echo "Prompt Tokens: $initial_prompt_tokens → $current_prompt_tokens"
        echo "Completion Tokens: $initial_completion_tokens → $current_completion_tokens"
        echo "Transaction Count: $initial_transaction_count → $current_transaction_count"
        echo "Last Updated: $initial_last_updated → $current_last_updated"
        
        # Check if values changed as expected
        # Token usage should increase (tests consume tokens)
        # Token balances should decrease (tokens were spent)
        # Transaction count should increase
        
        failed=false
        
        # Check prompt tokens increased
        if [ "$current_prompt_tokens" = "$initial_prompt_tokens" ]; then
            echo "❌ Prompt tokens did not change: $initial_prompt_tokens → $current_prompt_tokens"
            failed=true
        elif [ "$(echo "$current_prompt_tokens < $initial_prompt_tokens" | bc -l 2>/dev/null || echo "0")" = "1" ]; then
            echo "❌ Prompt tokens decreased unexpectedly: $initial_prompt_tokens → $current_prompt_tokens"
            failed=true
        else
            echo "✅ Prompt tokens increased as expected: $initial_prompt_tokens → $current_prompt_tokens"
        fi
        
        # Check completion tokens increased
        if [ "$current_completion_tokens" = "$initial_completion_tokens" ]; then
            echo "❌ Completion tokens did not change: $initial_completion_tokens → $current_completion_tokens"
            failed=true
        elif [ "$(echo "$current_completion_tokens < $initial_completion_tokens" | bc -l 2>/dev/null || echo "0")" = "1" ]; then
            echo "❌ Completion tokens decreased unexpectedly: $initial_completion_tokens → $current_completion_tokens"
            failed=true
        else
            echo "✅ Completion tokens increased as expected: $initial_completion_tokens → $current_completion_tokens"
        fi
        
        # Check transaction count increased
        if [ "$current_transaction_count" = "$initial_transaction_count" ]; then
            echo "❌ Transaction count did not change: $initial_transaction_count → $current_transaction_count"
            failed=true
        elif [ "$(echo "$current_transaction_count < $initial_transaction_count" | bc -l 2>/dev/null || echo "0")" = "1" ]; then
            echo "❌ Transaction count decreased unexpectedly: $initial_transaction_count → $current_transaction_count"
            failed=true
        else
            echo "✅ Transaction count increased as expected: $initial_transaction_count → $current_transaction_count"
        fi
        
        # Check token balance in decreased (tokens were spent)
        if [ "$current_token_balance_in" = "$initial_token_balance_in" ]; then
            echo "❌ Token balance in did not change: $initial_token_balance_in → $current_token_balance_in"
            failed=true
        elif [ "$(echo "$current_token_balance_in > $initial_token_balance_in" | bc -l 2>/dev/null || echo "0")" = "1" ]; then
            echo "⚠️ Token balance in increased: $initial_token_balance_in → $current_token_balance_in (may be expected for some account types)"
        else
            echo "✅ Token balance in decreased as expected: $initial_token_balance_in → $current_token_balance_in"
        fi
        
        # Check token balance out decreased (tokens were spent)
        if [ "$current_token_balance_out" = "$initial_token_balance_out" ]; then
            echo "❌ Token balance out did not change: $initial_token_balance_out → $current_token_balance_out"
            failed=true
        elif [ "$(echo "$current_token_balance_out > $initial_token_balance_out" | bc -l 2>/dev/null || echo "0")" = "1" ]; then
            echo "⚠️ Token balance out increased: $initial_token_balance_out → $current_token_balance_out (may be expected for some account types)"
        else
            echo "✅ Token balance out decreased as expected: $initial_token_balance_out → $current_token_balance_out"
        fi
        
        # Check total token usage in increased
        if [ "$(echo "$current_total_token_usage_in < $initial_total_token_usage_in" | bc -l 2>/dev/null || echo "0")" = "1" ]; then
            echo "❌ Total token usage in decreased unexpectedly: $initial_total_token_usage_in → $current_total_token_usage_in"
            failed=true
        elif [ "$current_total_token_usage_in" = "$initial_total_token_usage_in" ]; then
            echo "❌ Total token usage in did not change: $initial_total_token_usage_in → $current_total_token_usage_in"
            failed=true
        else
            echo "✅ Total token usage in increased as expected: $initial_total_token_usage_in → $current_total_token_usage_in"
        fi
        
        # Check total token usage out increased
        if [ "$(echo "$current_total_token_usage_out < $initial_total_token_usage_out" | bc -l 2>/dev/null || echo "0")" = "1" ]; then
            echo "❌ Total token usage out decreased unexpectedly: $initial_total_token_usage_out → $current_total_token_usage_out"
            failed=true
        elif [ "$current_total_token_usage_out" = "$initial_total_token_usage_out" ]; then
            echo "❌ Total token usage out did not change: $initial_total_token_usage_out → $current_total_token_usage_out"
            failed=true
        else
            echo "✅ Total token usage out increased as expected: $initial_total_token_usage_out → $current_total_token_usage_out"
        fi
        
        # Check last_updated changed
        if [ "$current_last_updated" = "$initial_last_updated" ] || [ -z "$current_last_updated" ]; then
            echo "❌ Last updated timestamp did not change: $initial_last_updated → $current_last_updated"
            failed=true
        else
            echo "✅ Last updated timestamp changed as expected"
        fi
        
        # Clean up temp files
        rm -f /tmp/token_balance_in_before /tmp/token_balance_out_before /tmp/word_balance_before \
              /tmp/total_token_usage_in_before /tmp/total_token_usage_out_before /tmp/total_word_usage_before \
              /tmp/prompt_tokens_before /tmp/completion_tokens_before /tmp/transaction_count_before \
              /tmp/last_updated_before
        
        if [ "$failed" = "true" ]; then
            echo "❌ Balance administration test failed"
            exit 1
        else
            echo "✅ Balance administration test passed"
        fi
        ;;
        
    *)
        echo "Usage: $0 {before|after}"
        echo "  before: Check and store initial balance values"
        echo "  after:  Check final balance values and compare with initial"
        exit 1
        ;;
esac 