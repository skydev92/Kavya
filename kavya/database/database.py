import logging
import os
import random
import threading
import time
from datetime import datetime
from typing import Any

from sqlalchemy import exc

from kavya.models import AccountTokenBalance, DailyUsageSummary

from .connection_pool import PostgreSQLConnectionPool
from .utils import generate_transaction_id

_ENV = os.getenv("ENVIRONMENT", "dev")


class Database:
    def __init__(self, config: dict = None) -> None:
        global _ENV
        if config is None:
            raise ValueError(
                "config is required for Database – no in-code defaults allowed"
            )

        self.config = config

        balance_config = config["database"]["default_balances"]
        self.default_token_balance_in = balance_config["token_balance_in"]
        self.default_token_balance_out = balance_config["token_balance_out"]
        self.default_word_balance = balance_config["word_balance"]

        logging.info(
            f"Initializing database in {'development' if _ENV == 'dev' else 'production'} environment"
        )

        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL environment variable is required")

        # Get environment-specific configuration
        instance_connection_name = os.getenv("INSTANCE_CONNECTION_NAME")
        private_ip = bool(os.getenv("PRIVATE_IP"))

        # If we already have a connection, close it properly
        try:
            self.pool = PostgreSQLConnectionPool(
                database_url=database_url,
                instance_connection_name=instance_connection_name,
                private_ip=private_ip,
                config=self.config,
            )
        except Exception as e:
            error_msg = f"Failed to initialize database connection: {type(e).__name__}: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg)

    def initialize_database(self) -> None:
        """
        Initialize database connection based on environment
        """
        with self.pool.open_session(True) as cursor:
            try:
                # Test that we can actually write to the database
                # Use a more resilient approach with shorter timeouts
                # First try a simple read query to verify basic connectivity
                cursor.execute("SELECT 1 as test")

                if cursor.fetchone()[0] != 1:
                    raise Exception("Failed to verify database read access")

                logging.info("Successfully verified database read access")

                # Try to insert a test record with a more resilient approach
                try:
                    _ensure_account_exists(
                        cursor,
                        "-999",
                        self.default_token_balance_in,
                        self.default_token_balance_out,
                        self.default_word_balance,
                    )

                    # Verify the account exists with a simple read
                    cursor.execute(
                        """
                        SELECT COUNT(*)
                        FROM account_totals
                        WHERE account_id = -999
                        """
                    )

                    count = cursor.fetchone()[0]
                    if count == 0:
                        logging.warning(
                            "Test account does not exist, but database is accessible"
                        )
                    else:
                        logging.info("Successfully verified test account exists")

                    # Removed database health check

                    # Apply VACUUM optimizations if needed
                    # self._apply_vacuum_optimizations(connection)

                    logging.info("Successfully verified database access")
                    return
                except Exception as write_error:
                    # If write fails but read succeeded, log warning but continue
                    logging.warning(
                        f"Database write test failed, but read succeeded: {str(write_error)}"
                    )
                    logging.info("Continuing with read-only verification")
                    return
            except Exception as e:
                logging.error(
                    f"Failed to verify database access: {type(e).__name__}: {str(e)}"
                )
                raise RuntimeError(
                    f"Database initialization failed - could not access database: {str(e)}"
                )

    def close(self) -> None:
        """
        Close the database connection pool and clean up resources.
        """
        self.pool.close()

    def update_usage_with_response(
        self,
        account_id: int,
        prompt_tokens: int,
        completion_tokens: int,
        word_count: int = 0,
    ) -> None:
        """Update account usage with response data.

        Args:
            account_id: The account ID to update
            prompt_tokens: The number of prompt tokens used
            completion_tokens: The number of completion tokens used
            word_count: The number of words generated (optional)
        """
        transaction_id = generate_transaction_id()
        thread_id = threading.get_ident()
        start_time = time.time()
        logging.info(
            f"🎯 DB_UPDATE_START: Thread-{thread_id} [ID: {transaction_id}] account={account_id}, "
            f"prompt={prompt_tokens}, completion={completion_tokens}, words={word_count}"
        )

        with self.pool.open_session(True, transaction_id=transaction_id) as cursor:
            _ensure_account_exists(
                cursor,
                account_id,
                self.default_token_balance_in,
                self.default_token_balance_out,
                self.default_word_balance,
            )

            cursor.execute(
                """
                UPDATE account_totals
                SET token_balance_in      = GREATEST(0, token_balance_in - %s),
                    token_balance_out     = GREATEST(0, token_balance_out - %s),
                    word_balance          = GREATEST(0, word_balance - %s),
                    transactions          = transactions + 1,
                    total_token_usage_in  = total_token_usage_in + %s,
                    total_token_usage_out = total_token_usage_out + %s,
                    total_word_usage      = total_word_usage + %s,
                    last_updated          = CURRENT_TIMESTAMP
                WHERE account_id = %s
                  AND token_balance_in >= %s
                  AND token_balance_out >= %s
                  AND word_balance >= %s RETURNING token_balance_in, token_balance_out, word_balance, 
                          transactions, total_token_usage_in, total_token_usage_out, 
                          total_word_usage
                """,
                (
                    # account_id,
                    prompt_tokens,
                    completion_tokens,
                    word_count,
                    prompt_tokens,
                    completion_tokens,
                    word_count,
                    account_id,
                    prompt_tokens,
                    completion_tokens,
                    word_count,
                ),
            )

            result = cursor.fetchone()
            if not result:
                # If no rows were updated, it means the account doesn't have enough balance
                logging.error(
                    f"[ID: {transaction_id}] Insufficient balance for account {account_id} "
                    f"(prompt: {prompt_tokens}, completion: {completion_tokens}, words: {word_count})"
                )
                raise RuntimeError(
                    f"Insufficient token balance for account {account_id}"
                )

        # Now update the daily summary in a new transaction to avoid failures affecting the balance update
        try:
            with self.pool.open_session(True, transaction_id=transaction_id) as cursor:
                today = datetime.now().strftime("%Y-%m-%d")

                cursor.execute(
                    """
                    INSERT INTO account_daily_summary
                    (account_id, date, transaction_count, daily_token_usage_in,
                     daily_token_usage_out, daily_word_usage)
                    VALUES (%s, %s, 1, %s, %s, %s) ON CONFLICT (account_id, date) DO
                    UPDATE SET
                        transaction_count = account_daily_summary.transaction_count + 1,
                        daily_token_usage_in = account_daily_summary.daily_token_usage_in + EXCLUDED.daily_token_usage_in,
                        daily_token_usage_out = account_daily_summary.daily_token_usage_out + EXCLUDED.daily_token_usage_out,
                        daily_word_usage = account_daily_summary.daily_word_usage + EXCLUDED.daily_word_usage
                    """,
                    (account_id, today, prompt_tokens, completion_tokens, word_count),
                )
        except Exception as daily_error:
            # If daily summary update fails but balance update succeeded, log warning but continue
            logging.warning(
                f"[ID: {transaction_id}] Daily summary update failed but balance updated: {str(daily_error)}"
            )

        # Success! Get the updated balance
        final_balance = {
            "token_balance_in": float(result[0]),
            "token_balance_out": float(result[1]),
            "word_balance": int(result[2]),
            "transactions": int(result[3]),
            "total_token_usage_in": float(result[4]),
            "total_token_usage_out": float(result[5]),
            "total_word_usage": int(result[6]),
        }

        duration = time.time() - start_time

        if duration > 10.0:
            logging.warning(
                f"🐌 DB_UPDATE_VERY_SLOW: Thread-{thread_id} [ID: {transaction_id}] completed in {duration:.2f}s! "
                f"Final balance: {final_balance}"
            )
        elif duration > 5.0:
            logging.warning(
                f"🐌 DB_UPDATE_SLOW: Thread-{thread_id} [ID: {transaction_id}] completed in {duration:.2f}s. "
                f"Final balance: {final_balance}"
            )
        else:
            logging.info(
                f"✅ DB_UPDATE_SUCCESS: Thread-{thread_id} [ID: {transaction_id}] completed in {duration:.2f}s. "
                f"Final balance: {final_balance}"
            )

    def get_account_balance(self, account_id: int):
        """Get current token balance for an account"""
        with self.pool.open_session(False) as cursor:
            cursor.execute(
                """
                SELECT token_balance_in, token_balance_out, transactions
                FROM account_totals
                WHERE account_id = %s
                """,
                (account_id,),
            )
            result = cursor.fetchone()

            if result:
                return {
                    "token_balance_in": result[0],
                    "token_balance_out": result[1],
                    "transactions": result[2],
                }

        return None

    def get_daily_usage(self, account_id: int, start_date: str, end_date: str):
        """Get daily usage for an account within a date range"""
        with self.pool.open_session(False) as cursor:
            cursor.execute(
                """
                SELECT date, transaction_count, daily_token_usage_in, daily_token_usage_out, daily_word_usage
                FROM account_daily_summary
                WHERE account_id = %s
                  AND date BETWEEN %s
                  AND %s
                ORDER BY date
                """,
                (account_id, start_date, end_date),
            )
            return cursor.fetchall()

    def check_sufficient_balance(
        self,
        account_id: int,
        prompt_tokens: int,
        completion_tokens: int,
        word_count: int = 0,
    ) -> tuple[bool, dict]:
        """Check if account has sufficient balance for the requested operation without updating the balance."""
        transaction_id = generate_transaction_id()

        with self.pool.open_session(True, transaction_id=transaction_id) as cursor:
            _ensure_account_exists(
                cursor,
                account_id,
                self.default_token_balance_in,
                self.default_token_balance_out,
                self.default_word_balance,
            )

            cursor.execute(
                """
                SELECT token_balance_in,
                       token_balance_out,
                       word_balance,
                       transactions
                FROM account_totals
                WHERE account_id = %s
                """,
                (account_id,),
            )

            result = cursor.fetchone()

            if not result:
                # If account doesn't exist after our insert attempt, something is wrong
                # But let's return a default balance instead of failing
                logging.error(
                    f"[ID: {transaction_id}] Account {account_id} not found, using default values"
                )
                return True, {
                    "token_balance_in": self.default_token_balance_in,
                    "token_balance_out": self.default_token_balance_out,
                    "word_balance": self.default_word_balance,
                    "transactions": 0,
                }

            # Calculate sufficient balance directly
            # For balance checks, it's better to assume sufficient balance than to fail the request
            token_balance_in = (
                float(result[0])
                if result[0] is not None
                else self.default_token_balance_in
            )
            token_balance_out = (
                float(result[1])
                if result[1] is not None
                else self.default_token_balance_out
            )
            word_balance = (
                int(result[2]) if result[2] is not None else self.default_word_balance
            )
            transactions = int(result[3]) if result[3] is not None else 0

            has_sufficient_balance = (
                token_balance_in >= prompt_tokens
                and token_balance_out >= completion_tokens
                and word_balance >= word_count
            )

            current_balance = {
                "token_balance_in": token_balance_in,
                "token_balance_out": token_balance_out,
                "word_balance": word_balance,
                "transactions": transactions,
            }

            return has_sufficient_balance, current_balance

    def get_account_balance_model(self, account_id: int) -> "AccountTokenBalance":
        """Get current token balance for an account as a Pydantic model"""
        with self.pool.open_session(True) as cursor:
            _ensure_account_exists(
                cursor,
                account_id,
                self.default_token_balance_in,
                self.default_token_balance_out,
                self.default_word_balance,
            )

            cursor.execute(
                """
                SELECT token_balance_in,
                       token_balance_out,
                       word_balance,
                       transactions,
                       total_token_usage_in,
                       total_token_usage_out,
                       total_word_usage
                FROM account_totals
                WHERE account_id = %s
                """,
                (account_id,),
            )
            result = cursor.fetchone()

            if not result:
                # If account doesn't exist after our insert attempt, something is wrong
                # But let's return a default balance instead of failing
                logging.error(f"Account {account_id} not found, using default values")
                return AccountTokenBalance(
                    account_id=account_id,
                    token_balance_in=self.default_token_balance_in,
                    token_balance_out=self.default_token_balance_out,
                    word_balance=self.default_word_balance,
                    transactions=0,
                    total_token_usage_in=0.0,
                    total_token_usage_out=0.0,
                    total_word_usage=0,
                )

            return AccountTokenBalance(
                account_id=account_id,
                token_balance_in=float(result[0]),
                token_balance_out=float(result[1]),
                word_balance=int(result[2]),
                transactions=int(result[3]),
                total_token_usage_in=float(result[4]),
                total_token_usage_out=float(result[5]),
                total_word_usage=int(result[6]),
            )

    def get_daily_usage_model(
        self, account_id: int, start_date: str, end_date: str
    ) -> list["DailyUsageSummary"]:
        """Get daily usage for an account within a date range as Pydantic models"""
        with self.pool.open_session(False) as cursor:
            _ensure_account_exists(
                cursor,
                account_id,
                self.default_token_balance_in,
                self.default_token_balance_out,
                self.default_word_balance,
            )

            cursor.execute(
                """
                SELECT date, transaction_count, daily_token_usage_in, daily_token_usage_out, daily_word_usage
                FROM account_daily_summary
                WHERE account_id = %s
                  AND date BETWEEN %s
                  AND %s
                ORDER BY date
                """,
                (account_id, start_date, end_date),
            )

            results = []

            for row in cursor.fetchall():
                # Convert date to string in YYYY-MM-DD format
                date_str = (
                    row[0].strftime("%Y-%m-%d")
                    if hasattr(row[0], "strftime")
                    else str(row[0])
                )

                results.append(
                    DailyUsageSummary(
                        account_id=account_id,
                        date=date_str,
                        transaction_count=int(row[1]),
                        daily_token_usage_in=float(row[2]),
                        daily_token_usage_out=float(row[3]),
                        daily_word_usage=int(row[4]),
                    )
                )

            return results


def _ensure_account_exists(
    cursor,
    account_id,
    default_token_balance_in,
    default_token_balance_out,
    default_word_balance,
) -> None:
    cursor.execute(
        """
        INSERT INTO account_totals
        (account_id, token_balance_in, token_balance_out, word_balance,
         total_token_usage_in, total_token_usage_out, total_word_usage, transactions)
        VALUES (%s, %s, %s, %s, 0, 0, 0, 0) ON CONFLICT (account_id) DO NOTHING
        """,
        (
            account_id,
            default_token_balance_in,
            default_token_balance_out,
            default_word_balance,
        ),
    )


def _check_error_message(
    error_type: Any, error_msg: str, backoff_base: float, retry_count: int
):
    # Check for specific errors that might be retryable
    is_lock_timeout = "lock timeout" in error_msg.lower() or "55P03" in error_msg
    is_statement_timeout = (
        "statement timeout" in error_msg.lower() or "57014" in error_msg
    )
    is_deadlock = "deadlock detected" in error_msg.lower() or "40P01" in error_msg
    is_serialize_failure = (
        "could not serialize access" in error_msg.lower() or "40001" in error_msg
    )
    is_transaction_aborted = (
        "current transaction is aborted" in error_msg.lower() or "25P02" in error_msg
    )
    is_failed_transaction = "in failed transaction" in error_msg.lower()
    is_lock_contention = "could not acquire lock" in error_msg.lower()
    # Add check for network/connection errors based on type
    is_network_error = (
        isinstance(
            error_type,
            (
                exc.InterfaceError,
                exc.OperationalError,
                exc.TimeoutError,
                exc.DisconnectionError,
            ),
        )
        or "network error" in error_msg.lower()
    )

    # For recoverable errors, retry with backoff
    is_retryable = (
        is_lock_timeout
        or is_statement_timeout
        or is_deadlock
        or is_serialize_failure
        or is_transaction_aborted
        or is_failed_transaction
        or is_lock_contention
        or is_network_error
    )
    wait_time = None
    error_category = "unknown"

    if is_retryable:
        # Shorter exponential backoff with small random jitter
        backoff = backoff_base * (
            1.5 ** (retry_count - 1)
        )  # Less aggressive exponential growth
        jitter = random.uniform(0, backoff * 0.2)  # 20% jitter
        wait_time = backoff + jitter  # Calculate wait time in milliseconds
        # Ensure wait_time is never None if is_retryable is True
        if wait_time is None:
            wait_time = (
                backoff_base  # Default to base backoff if calculation failed somehow
            )

        # Log specific error type for better debugging
        if is_lock_timeout:
            error_category = "lock timeout"
        elif is_statement_timeout:
            error_category = "statement timeout"
        elif is_deadlock:
            error_category = "deadlock"
        elif is_serialize_failure:
            error_category = "serialization failure"
        elif is_transaction_aborted or is_failed_transaction:
            error_category = "transaction aborted"
        elif is_lock_contention:
            error_category = "lock contention"

    # Safe calculation of wait_time_seconds - avoid NoneType division error
    if wait_time is not None:
        wait_time_seconds = wait_time / 1000.0
    else:
        wait_time_seconds = 0.0

    return {
        "is_retryable": is_retryable,
        "error_category": error_category,
        "wait_time": wait_time,
        "wait_time_seconds": wait_time_seconds,
    }
