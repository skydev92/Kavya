WITH updated_totals AS (
    UPDATE account_totals 
    SET token_balance_in = token_balance_in + $1,
        token_balance_out = token_balance_out + $2,
        word_balance = word_balance + $3,
        total_token_usage_in = total_token_usage_in + $4,
        total_token_usage_out = total_token_usage_out + $5,
        total_word_usage = total_word_usage + $6,
        transactions = transactions + 1
    WHERE account_id = $7
        AND token_balance_in >= $4
        AND token_balance_out >= $5
        AND word_balance >= $6
        AND pg_try_advisory_xact_lock($7)  -- Try to acquire advisory lock
    RETURNING account_id, token_balance_in, token_balance_out, word_balance, 
              total_token_usage_in, total_token_usage_out, total_word_usage, transactions
),
updated_daily AS (
    INSERT INTO account_daily_summary (
        account_id,
        date,
        daily_token_usage_in,
        daily_token_usage_out,
        daily_word_usage,
        transaction_count,
        last_updated
    )
    SELECT 
        $7,  -- account_id
        $8::date,  -- date
        $4,  -- daily_token_usage_in
        $5,  -- daily_token_usage_out
        $6,  -- daily_word_usage
        1,   -- transaction_count
        CURRENT_TIMESTAMP
    WHERE EXISTS (SELECT 1 FROM updated_totals)  -- Only proceed if totals update succeeded
    ON CONFLICT (account_id, date) DO UPDATE
    SET daily_token_usage_in = account_daily_summary.daily_token_usage_in + EXCLUDED.daily_token_usage_in,
        daily_token_usage_out = account_daily_summary.daily_token_usage_out + EXCLUDED.daily_token_usage_out,
        daily_word_usage = account_daily_summary.daily_word_usage + EXCLUDED.daily_word_usage,
        transaction_count = account_daily_summary.transaction_count + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE account_daily_summary.account_id = EXCLUDED.account_id
        AND account_daily_summary.date = EXCLUDED.date
    RETURNING account_id, daily_token_usage_in, daily_token_usage_out, daily_word_usage, transaction_count
)
SELECT 
    t.account_id,
    t.token_balance_in,
    t.token_balance_out,
    t.word_balance,
    t.total_token_usage_in,
    t.total_token_usage_out,
    t.total_word_usage,
    t.transactions as total_transactions,
    d.daily_token_usage_in,
    d.daily_token_usage_out,
    d.daily_word_usage,
    d.transaction_count as daily_transactions
FROM updated_totals t
LEFT JOIN updated_daily d ON t.account_id = d.account_id; 