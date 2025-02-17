WITH updated_totals AS (
    UPDATE account_totals 
    SET token_in = token_in + $1,
        token_out = token_out + $2,
        transactions = transactions + 1
    WHERE account_id = $3
    AND token_in - token_out >= $4 + $5
    RETURNING account_id
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
    VALUES (
        $6, $7, $8, $9, 1, CURRENT_TIMESTAMP
    )
    ON CONFLICT (account_id, date) DO UPDATE
    SET token_in = account_daily_summary.token_in + EXCLUDED.token_in,
        token_out = account_daily_summary.token_out + EXCLUDED.token_out,
        transaction_count = account_daily_summary.transaction_count + 1,
        last_updated = CURRENT_TIMESTAMP
    WHERE account_daily_summary.account_id = EXCLUDED.account_id
    AND account_daily_summary.date = EXCLUDED.date
    RETURNING account_id
)
SELECT account_id FROM updated_totals; 