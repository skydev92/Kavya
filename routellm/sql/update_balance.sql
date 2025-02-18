WITH updated_totals AS (
    UPDATE account_totals 
    SET token_in = token_in + $1,
        token_out = token_out + $2,
        transactions = transactions + 1
    WHERE account_id = $3
        AND token_in - token_out >= $4 + $5
        AND pg_try_advisory_xact_lock($3)  -- Try to acquire advisory lock
    RETURNING account_id, token_in, token_out, transactions
),
updated_daily AS (
    INSERT INTO account_daily_summary (
        account_id,
        date,
        token_in,
        token_out,
        transaction_count,
        last_updated
    )
    SELECT 
        $6, 
        $7::date, 
        $8, 
        $9, 
        1, 
        CURRENT_TIMESTAMP
    WHERE EXISTS (SELECT 1 FROM updated_totals)  -- Only proceed if totals update succeeded
    ON CONFLICT (account_id, date) DO UPDATE
    SET token_in = account_daily_summary.token_in + EXCLUDED.token_in,
        token_out = account_daily_summary.token_out + EXCLUDED.token_out,
        transaction_count = account_daily_summary.transaction_count + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE account_daily_summary.account_id = EXCLUDED.account_id
        AND account_daily_summary.date = EXCLUDED.date
    RETURNING account_id, token_in, token_out, transaction_count
)
SELECT 
    t.account_id,
    t.token_in as total_token_in,
    t.token_out as total_token_out,
    t.transactions as total_transactions,
    d.token_in as daily_token_in,
    d.token_out as daily_token_out,
    d.transaction_count as daily_transactions
FROM updated_totals t
LEFT JOIN updated_daily d ON t.account_id = d.account_id; 