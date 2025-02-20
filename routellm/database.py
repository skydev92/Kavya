import os
from datetime import datetime
import logging
import threading
from contextlib import contextmanager
import time
from urllib.parse import urlparse
import random
from sqlalchemy import event, exc
from sqlalchemy.sql import text

# Check for required PostgreSQL dependencies
required_deps = ['google.cloud.sql.connector', 'pg8000', 'sqlalchemy']
if os.getenv("INSTANCE_CONNECTION_NAME"):  # If using Cloud SQL
    for dep in required_deps:
        try:
            __import__(dep.split('.')[0])
        except ImportError:
            error_msg = f"CRITICAL: Production environment requires PostgreSQL dependencies but {dep} is missing"
            logging.critical(error_msg)
            raise RuntimeError(error_msg)

from google.cloud.sql.connector import Connector, IPTypes
import pg8000
import sqlalchemy

# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class DatabaseConnection:
    def __init__(self):
        self.connection = None
        self.cursor = None
        self.engine = None
        self.isolation_level = "READ COMMITTED"
        self._in_transaction = False
        self._deadlock_retries = 3
        self._deadlock_wait = 0.1  # Initial wait time in seconds

    def connect(self):
        raise NotImplementedError

    def get_cursor(self):
        raise NotImplementedError

    def begin_transaction(self):
        raise NotImplementedError

    def commit(self):
        raise NotImplementedError

    def rollback(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

class PostgreSQLConnection(DatabaseConnection):
    def __init__(self, database_url, instance_connection_name=None, private_ip=False):
        super().__init__()  # Call parent class initialization
        self.database_url = database_url
        self.instance_connection_name = instance_connection_name
        self.private_ip = private_ip
        self.db_host = None
        self.db_port = None
        self.db_user = None
        self.db_pass = None
        self.db_name = None
        # Add configuration from environment variables with defaults
        self.pool_size = int(os.getenv("DB_POOL_SIZE", "20"))
        self.max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        self.pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        self.pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "1800"))
        self.max_retries = int(os.getenv("DB_MAX_RETRIES", "5"))
        self.retry_backoff = float(os.getenv("DB_RETRY_BACKOFF", "0.1"))
        
        if instance_connection_name:
            # Parse credentials from DATABASE_URL for Cloud SQL
            parsed = urlparse(database_url)
            self.db_user = parsed.username
            self.db_pass = parsed.password
            self.db_name = parsed.path.lstrip('/')

    def connect(self):
        if self.instance_connection_name:  # Production mode with Cloud SQL
            connector = Connector(refresh_strategy="LAZY")
            def getconn():
                conn = connector.connect(
                    self.instance_connection_name,
                    "pg8000",
                    user=self.db_user,
                    password=self.db_pass,
                    db=self.db_name,
                    ip_type=IPTypes.PRIVATE if self.private_ip else IPTypes.PUBLIC,
                )
                # Set autocommit temporarily to configure session
                conn.autocommit = True
                cursor = conn.cursor()
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SET statement_timeout = '10s'")
                # Restore autocommit to False for normal operations
                conn.autocommit = False
                return conn

            self.engine = sqlalchemy.create_engine(
                "postgresql+pg8000://",
                creator=getconn,
                pool_size=self.pool_size,
                max_overflow=self.max_overflow,
                pool_timeout=self.pool_timeout,
                pool_recycle=self.pool_recycle,
                pool_pre_ping=True,
                isolation_level="READ COMMITTED"
            )
        else:  # Development mode with local PostgreSQL
            self.engine = sqlalchemy.create_engine(
                self.database_url,
                pool_size=self.pool_size,
                max_overflow=self.max_overflow,
                pool_timeout=self.pool_timeout,
                pool_recycle=self.pool_recycle,
                pool_pre_ping=True,
                isolation_level="READ COMMITTED"
            )

        # Configure retry handling using event listeners
        @event.listens_for(self.engine, "handle_error")
        def handle_error(context):
            error = context.original_exception
            connection = context.connection
            
            is_disconnect = isinstance(error, (
                exc.DisconnectionError,
                exc.OperationalError,
                exc.TimeoutError
            ))
            
            if is_disconnect:
                # Invalidate and retry for connection errors
                connection.invalidate()
                return True
                
            is_deadlock = any(msg in str(error).lower() for msg in [
                "deadlock detected",
                "could not serialize access",
                "concurrent update",
                "lock timeout"
            ])
            
            if is_deadlock:
                # Add exponential backoff for deadlocks
                retries = getattr(context, '_retry_count', 0)
                if retries < self.max_retries:
                    context._retry_count = retries + 1
                    # Exponential backoff with jitter
                    delay = self.retry_backoff * (2 ** retries) + random.uniform(0, 0.1)
                    time.sleep(delay)
                    logging.warning(f"Deadlock detected, retrying (attempt {retries + 1}/{self.max_retries})")
                    return True
                logging.error(f"Max retries ({self.max_retries}) exceeded for deadlock")
            return False

        try:
            self.connection = self.engine.raw_connection()
            # Set autocommit temporarily to configure session
            self.connection.autocommit = True
            cursor = self.get_cursor()
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cursor.execute("SET lock_timeout = '5s'")
            cursor.execute("SET statement_timeout = '10s'")
            # Restore autocommit to False for normal operations
            self.connection.autocommit = False
            return self.connection
        except Exception as e:
            if self.connection:
                self.connection.close()
            if self.engine:
                self.engine.dispose()
            raise RuntimeError(f"Failed to establish database connection: {str(e)}")

    def get_cursor(self):
        if not self.connection:
            raise RuntimeError("No connection established. Call connect() first.")
        if not self.cursor:
            self.cursor = self.connection.cursor()
        return self.cursor

    def begin_transaction(self):
        if not self._in_transaction:
            cursor = self.get_cursor()
            cursor.execute("BEGIN TRANSACTION")
            self._in_transaction = True

    def commit(self):
        if self._in_transaction:
            self.connection.commit()
            self._in_transaction = False

    def rollback(self):
        if self._in_transaction:
            self.connection.rollback()
            self._in_transaction = False

    def close(self):
        if self.cursor:
            self.cursor.close()
            self.cursor = None
        if self.connection:
            self.connection.close()
            self.connection = None
        if self.engine:
            self.engine.dispose()

class Database:
    # SQL Templates standardized for PostgreSQL
    SQL_TEMPLATES = {
        'upsert_account': """
            INSERT INTO account_totals (account_id)
            VALUES (%s)
            ON CONFLICT (account_id) DO UPDATE SET
                account_id = account_totals.account_id
            RETURNING token_in, token_out, transactions
        """,
        'update_balance': """
            WITH updated_totals AS (
                UPDATE account_totals SET
                    token_in = token_in - %s,
                    token_out = token_out - %s,
                    transactions = transactions + 1
                WHERE account_id = %s
                    AND token_in >= %s 
                    AND token_out >= %s
                RETURNING token_in, token_out, transactions
            ),
            daily_update AS (
                INSERT INTO account_daily_summary (account_id, date, token_in, token_out, transaction_count)
                VALUES (%s, %s::date, %s, %s, 1)
                ON CONFLICT(account_id, date) DO UPDATE SET
                    transaction_count = account_daily_summary.transaction_count + 1,
                    token_in = account_daily_summary.token_in + EXCLUDED.token_in,
                    token_out = account_daily_summary.token_out + EXCLUDED.token_out,
                    last_updated = CURRENT_TIMESTAMP
            )
            SELECT * FROM updated_totals
        """,
        'update_daily': """
            INSERT INTO account_daily_summary 
                (account_id, date, token_in, token_out, transaction_count)
            VALUES (%s, %s::date, %s, %s, 1)
            ON CONFLICT(account_id, date) DO UPDATE SET
                transaction_count = COALESCE(account_daily_summary.transaction_count, 0) + 1,
                token_in = account_daily_summary.token_in + EXCLUDED.token_in,
                token_out = account_daily_summary.token_out + EXCLUDED.token_out,
                last_updated = CURRENT_TIMESTAMP
        """,
        'check_balance': """
            SELECT token_in, token_out, transactions
            FROM account_totals
            WHERE account_id = %s;
        """
    }

    def __init__(self):
        self.env = os.getenv("ENVIRONMENT", "dev")
        self._local = threading.local()
        self.param_style = "%s"  # Always use PostgreSQL style
        self._update_lock = threading.Lock()  # Add global lock for updates
        logging.info(f"Initializing database in {'development' if self.env == 'dev' else 'production'} environment")
        self.initialize_database()

    def _get_connection(self):
        """Get thread-local connection with proper thread safety settings"""
        if not hasattr(self._local, 'db'):
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL environment variable is required")

            # For Cloud SQL, we also need the instance connection name
            instance_connection_name = os.getenv("INSTANCE_CONNECTION_NAME")
            private_ip = bool(os.getenv("PRIVATE_IP"))

            try:
                self._local.db = PostgreSQLConnection(
                    database_url=database_url,
                    instance_connection_name=instance_connection_name,
                    private_ip=private_ip
                )
                self._local.db.connect()
                self._ensure_tables_exist(self._local.db)
            except Exception as e:
                error_msg = f"Failed to initialize database connection: {str(e)}"
                logging.error(error_msg)
                raise RuntimeError(error_msg)

        return self._local.db

    def initialize_database(self):
        """Initialize database connection based on environment"""
        try:
            connection = self._get_connection()
            
            # Test that we can actually write to the database
            with self.get_transaction() as db:
                cursor = db.get_cursor()
                # Try to insert a test record
                cursor.execute(self.SQL_TEMPLATES['upsert_account'], (-999,))
                
                result = cursor.fetchone()
                if not result:
                    raise Exception("Failed to verify database write access - no result returned")
                    
                logging.info("Successfully verified database write access")
                
        except Exception as e:
            logging.error(f"Failed to verify database write access: {str(e)}")
            raise RuntimeError(f"Database initialization failed - could not write to database: {str(e)}")

    def _ensure_tables_exist(self, connection):
        """Ensure necessary tables exist without dropping existing ones"""
        cursor = connection.get_cursor()
        
        # Create account_totals table if it doesn't exist
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_totals (
            account_id INTEGER PRIMARY KEY,
            token_in NUMERIC(18,6) NOT NULL DEFAULT 3000000 CHECK (token_in >= 0),
            token_out NUMERIC(18,6) NOT NULL DEFAULT 1000000 CHECK (token_out >= 0),
            transactions INTEGER NOT NULL DEFAULT 0 CHECK (transactions >= 0),
            word_count INTEGER NOT NULL DEFAULT 0 CHECK (word_count >= 0)
        )
        """)

        # Create account_daily_summary table if it doesn't exist
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_daily_summary (
            account_id INTEGER NOT NULL REFERENCES account_totals(account_id),
            date DATE NOT NULL,
            transaction_count INTEGER NOT NULL DEFAULT 0 CHECK (transaction_count >= 0),
            token_in NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (token_in >= 0),
            token_out NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (token_out >= 0),
            word_count INTEGER NOT NULL DEFAULT 0 CHECK (word_count >= 0),
            last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (account_id, date)
        )
        """)
        
        # Create indices for better performance
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_summary_date 
        ON account_daily_summary(date)
        """)
        
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_summary_last_updated 
        ON account_daily_summary(last_updated)
        """)
        
        if self.env == "dev":
            connection.commit()

    @contextmanager
    def get_transaction(self):
        """Get a transaction context manager for safe database operations"""
        db = self._get_connection()
        try:
            db.begin_transaction()
            yield db
            db.commit()
        except Exception as e:
            try:
                db.rollback()
            except Exception as rollback_error:
                logging.error(f"Error during rollback: {str(rollback_error)}")
            logging.error(f"Transaction failed: {str(e)}")
            raise

    def _get_sql(self, template_name: str) -> str:
        """Get SQL query with correct parameter style for current environment."""
        return self.SQL_TEMPLATES[template_name].format(
            placeholder=self.param_style
        ).strip()

    def update_usage_with_response(self, account_id: int, prompt_tokens: int, completion_tokens: int, word_count: int = 0) -> None:
        """Update token usage in the database with proper transaction handling."""
        transaction_id = f"txn-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
        logging.info(f"=== DATABASE UPDATE START [ID: {transaction_id}] ===")
        logging.info(f"Account ID: {account_id}")
        logging.info(f"Prompt Tokens: {prompt_tokens}")
        logging.info(f"Completion Tokens: {completion_tokens}")
        logging.info(f"Word Count: {word_count}")

        max_retries = 5
        base_delay = 0.2
        max_delay = 2.0
        attempt = 0

        while attempt < max_retries:
            try:
                with self.get_transaction() as connection:
                    cursor = connection.get_cursor()
                    # Get current balance with row-level locking
                    cursor.execute("""
                        SELECT token_in, token_out, transactions, word_count 
                        FROM account_totals 
                        WHERE account_id = %s
                        FOR UPDATE NOWAIT
                        """, (account_id,))
                    current_balance = cursor.fetchone()

                    if not current_balance:
                        # Initialize account if it doesn't exist
                        cursor.execute("""
                            INSERT INTO account_totals (account_id, token_in, token_out, transactions, word_count)
                            VALUES (%s, 3000000, 1000000, 0, 0)
                            ON CONFLICT (account_id) DO UPDATE 
                            SET token_in = EXCLUDED.token_in
                            RETURNING token_in, token_out, transactions, word_count
                            """, (account_id,))
                        current_balance = cursor.fetchone()

                    current_balance_dict = {
                        "token_in": float(current_balance[0]),
                        "token_out": float(current_balance[1]),
                        "transactions": int(current_balance[2]),
                        "word_count": int(current_balance[3])
                    }
                    logging.info(f"[ID: {transaction_id}] Current Balance: {current_balance_dict}")

                    # Update account totals with explicit check
                    cursor.execute("""
                        UPDATE account_totals 
                        SET token_in = token_in - %s,
                            token_out = token_out - %s,
                            transactions = transactions + 1,
                            word_count = word_count + %s
                        WHERE account_id = %s
                        AND token_in >= %s
                        AND token_out >= %s
                        RETURNING token_in, token_out, transactions, word_count;
                        """, (
                            prompt_tokens,
                            completion_tokens,
                            word_count,
                            account_id,
                            prompt_tokens,
                            completion_tokens
                        ))

                    if cursor.rowcount == 0:
                        error_msg = "Insufficient token balance"
                        logging.error(f"=== DATABASE UPDATE FAILED [ID: {transaction_id}]: {error_msg} ===")
                        raise InsufficientTokensError(
                            message=error_msg,
                            current_balance=current_balance_dict,
                            required_tokens=TokenUsageUpdate(
                                account_id=account_id,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens
                            )
                        )

                    # Update daily summary in the same transaction
                    today = datetime.now().strftime("%Y-%m-%d")
                    cursor.execute("""
                        INSERT INTO account_daily_summary (
                            account_id, date, token_in, token_out, 
                            transaction_count, word_count, last_updated
                        )
                        VALUES (
                            %s, %s, %s, %s,
                            1, %s, CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (account_id, date) 
                        DO UPDATE SET
                            token_in = account_daily_summary.token_in + %s,
                            token_out = account_daily_summary.token_out + %s,
                            transaction_count = account_daily_summary.transaction_count + 1,
                            word_count = account_daily_summary.word_count + %s,
                            last_updated = CURRENT_TIMESTAMP;
                        """, (
                            account_id,
                            today,
                            prompt_tokens,
                            completion_tokens,
                            word_count,
                            prompt_tokens,
                            completion_tokens,
                            word_count
                        ))

                    # Log final balances after successful update
                    cursor.execute("""
                        SELECT token_in, token_out, transactions, word_count 
                        FROM account_totals 
                        WHERE account_id = %s
                        """, (account_id,))
                    final_balance = cursor.fetchone()
                    final_balance_dict = {
                        "token_in": float(final_balance[0]),
                        "token_out": float(final_balance[1]),
                        "transactions": int(final_balance[2]),
                        "word_count": int(final_balance[3])
                    }
                    
                    logging.info(f"=== DATABASE UPDATE SUCCEEDED [ID: {transaction_id}] ===")
                    logging.info(f"[ID: {transaction_id}] Final Balance: {final_balance_dict}")
                    return

            except Exception as e:
                attempt += 1
                error_msg = str(e).lower()
                error_details = getattr(e, 'diag', str(e))
                
                if attempt < max_retries and (
                    "could not obtain lock" in error_msg or
                    "deadlock detected" in error_msg or
                    "lock timeout" in error_msg
                ):
                    # Calculate delay with jitter
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    jitter = random.uniform(0, 0.1)  # Add up to 100ms of random jitter
                    sleep_time = delay + jitter
                    
                    logging.warning(
                        f"=== DATABASE UPDATE RETRY [ID: {transaction_id}] {attempt}/{max_retries} ===\n"
                        f"Error: {error_details}\n"
                        f"Retrying in {sleep_time:.2f}s"
                    )
                    time.sleep(sleep_time)
                    continue
                else:
                    if "insufficient" in error_msg:
                        logging.error(f"=== DATABASE UPDATE FAILED [ID: {transaction_id}]: Insufficient tokens ===\n{error_details}")
                        raise ValueError(error_msg)
                    else:
                        logging.error(f"=== DATABASE UPDATE FAILED [ID: {transaction_id}]: Database error ===\n{error_details}")
                        raise RuntimeError(f"Failed to update token usage: {error_details}")

        logging.error(f"=== DATABASE UPDATE FAILED [ID: {transaction_id}]: Max retries ({max_retries}) exceeded ===")
        raise RuntimeError(f"Failed to update usage after {max_retries} attempts")

    def get_account_balance(self, account_id: int):
        """Get current token balance for an account"""
        connection = self._get_connection()
        cursor = connection.get_cursor()
        cursor.execute("""
        SELECT token_in, token_out, transactions 
        FROM account_totals 
        WHERE account_id = %s
        """, (account_id,))
        result = cursor.fetchone()
        if result:
            return {
                "token_in": result[0],
                "token_out": result[1],
                "transactions": result[2]
            }
        return None

    def get_daily_usage(self, account_id: int, start_date: str, end_date: str):
        """Get daily usage for an account within a date range"""
        connection = self._get_connection()
        cursor = connection.get_cursor()
        cursor.execute("""
        SELECT date, transaction_count, token_in, token_out 
        FROM account_daily_summary 
        WHERE account_id = %s AND date BETWEEN %s AND %s
        ORDER BY date
        """, (account_id, start_date, end_date))
        return cursor.fetchall()

    def close(self):
        """Close all thread-local database connections"""
        if hasattr(self._local, 'db'):
            self._local.db.close()
            del self._local.db

    def create_tables(self):
        """Create the necessary tables if they don't exist"""
        try:
            with self.get_transaction() as connection:
                cursor = connection.get_cursor()
                
                # Drop existing tables to ensure clean schema
                cursor.execute("DROP TABLE IF EXISTS account_daily_summary")
                cursor.execute("DROP TABLE IF EXISTS account_totals")
                
                # Create account_totals table
                cursor.execute("""
                CREATE TABLE account_totals (
                    account_id INTEGER PRIMARY KEY,
                    token_in NUMERIC(18,6) NOT NULL DEFAULT 3000000 CHECK (token_in >= 0),
                    token_out NUMERIC(18,6) NOT NULL DEFAULT 1000000 CHECK (token_out >= 0),
                    transactions INTEGER NOT NULL DEFAULT 0 CHECK (transactions >= 0),
                    word_count INTEGER NOT NULL DEFAULT 0 CHECK (word_count >= 0)
                )
                """)

                # Create account_daily_summary table with composite primary key
                cursor.execute("""
                CREATE TABLE account_daily_summary (
                    account_id INTEGER NOT NULL REFERENCES account_totals(account_id),
                    date DATE NOT NULL,
                    transaction_count INTEGER NOT NULL DEFAULT 0 CHECK (transaction_count >= 0),
                    token_in NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (token_in >= 0),
                    token_out NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (token_out >= 0),
                    word_count INTEGER NOT NULL DEFAULT 0 CHECK (word_count >= 0),
                    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (account_id, date)
                )
                """)
                
                # Create indices for better query performance
                cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_summary_date 
                ON account_daily_summary(date)
                """)
                
                cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_summary_last_updated 
                ON account_daily_summary(last_updated)
                """)

        except Exception as e:
            logging.error(f"Error creating tables: {str(e)}")
            raise

    def check_sufficient_balance(self, account_id: int, prompt_tokens: int, completion_tokens: int) -> tuple[bool, dict]:
        """Check if account has sufficient balance for the requested operation without updating the balance."""
        try:
            with self.get_transaction() as connection:
                cursor = connection.get_cursor()
                
                # First ensure account exists and get current balance
                cursor.execute(
                    self._get_sql('upsert_account'),
                    (account_id,)
                )
                result = cursor.fetchone()
                if not result:
                    raise RuntimeError("Failed to get or create account")
                
                current_balance = {
                    "token_in": float(result[0]),
                    "token_out": float(result[1]),
                    "transactions": int(result[2])
                }
                
                # Check if balance is sufficient
                has_sufficient_balance = (
                    current_balance["token_in"] >= prompt_tokens and 
                    current_balance["token_out"] >= completion_tokens
                )
                
                return has_sufficient_balance, current_balance
                
        except Exception as e:
            logging.error(f"Error checking balance: {str(e)}")
            raise

    def initialize_test_accounts(self, account_ids: list[int]):
        """Initialize test accounts with default balances. Only available in development environment."""
        if self.env != "dev":
            raise RuntimeError("Test account initialization is only allowed in development environment")
        
        try:
            with self.get_transaction() as connection:
                cursor = connection.get_cursor()
                
                for account_id in account_ids:
                    cursor.execute(
                        """
                        INSERT INTO account_totals (account_id, token_in, token_out, transactions)
                        VALUES (?, 3000000, 1000000, 0)
                        ON CONFLICT(account_id) DO UPDATE SET
                            token_in = 3000000,
                            token_out = 1000000,
                            transactions = 0
                        """,
                        (account_id,)
                    )
                
                if self.env == "dev":
                    connection.commit()
                
                logging.info(f"Initialized {len(account_ids)} test accounts with default balances")
        
        except Exception as e:
            logging.error(f"Error initializing test accounts: {str(e)}")
            raise

    def reset_database_for_testing(self):
        """Reset database by dropping and recreating all tables. USE WITH CAUTION - THIS WILL DELETE ALL DATA!"""
        if self.env != "dev":
            raise RuntimeError("Database reset is only allowed in development environment")
        
        with self.get_transaction() as connection:
            cursor = connection.get_cursor()
            
            # Drop existing tables
            cursor.execute("DROP TABLE IF EXISTS account_daily_summary")
            cursor.execute("DROP TABLE IF EXISTS account_totals")
            
            # Recreate tables
            self._ensure_tables_exist(connection)
            
            # Initialize some test accounts
            self.initialize_test_accounts([20026, 20027, 20028])

    def get_account_balance_model(self, account_id: int) -> 'AccountTokenBalance':
        """Get current token balance for an account as a Pydantic model"""
        from routellm.models import AccountTokenBalance
        
        connection = self._get_connection()
        cursor = connection.get_cursor()
        cursor.execute("""
        SELECT token_in, token_out, transactions 
        FROM account_totals 
        WHERE account_id = %s
        """, (account_id,))
        result = cursor.fetchone()
        
        if result:
            return AccountTokenBalance(
                account_id=account_id,
                token_in=float(result[0]),
                token_out=float(result[1]),
                transactions=int(result[2])
            )
        
        # If no result, create a new account with default values
        cursor.execute(
            self._get_sql('check_balance'),
            (account_id, account_id, account_id)  # Pass account_id three times for %s placeholders
        )
        result = cursor.fetchone()
        if not result:
            raise RuntimeError("Failed to get or create account balance")
            
        return AccountTokenBalance(
            account_id=account_id,
            token_in=float(result[0]),
            token_out=float(result[1]),
            transactions=int(result[2])
        )

    def get_daily_usage_model(self, account_id: int, start_date: str, end_date: str) -> list['DailyUsageSummary']:
        """Get daily usage for an account within a date range as Pydantic models"""
        from routellm.models import DailyUsageSummary
        
        connection = self._get_connection()
        cursor = connection.get_cursor()
        cursor.execute("""
        SELECT date, transaction_count, token_in, token_out 
        FROM account_daily_summary 
        WHERE account_id = %s AND date BETWEEN %s AND %s
        ORDER BY date
        """, (account_id, start_date, end_date))
        
        results = []
        for row in cursor.fetchall():
            results.append(DailyUsageSummary(
                account_id=account_id,
                date=row[0],
                transaction_count=int(row[1]),
                token_in=float(row[2]),
                token_out=float(row[3]),
                last_updated=row[4]
            ))
        return results