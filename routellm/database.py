import os
from datetime import datetime
import logging
import threading
from contextlib import contextmanager
import time
from urllib.parse import urlparse

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
        self.isolation_level = "REPEATABLE READ"
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
    def __init__(self, database_url=None, instance_connection_name=None, private_ip=False):
        super().__init__()
        self.database_url = database_url
        self.instance_connection_name = instance_connection_name
        self.private_ip = private_ip
        self.db_user = None
        self.db_pass = None
        self.db_name = None
        
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
                return conn

            self.engine = sqlalchemy.create_engine(
                "postgresql+pg8000://",
                creator=getconn,
                pool_size=5,
                max_overflow=2,
                pool_timeout=30,
                pool_recycle=1800,
                isolation_level=self.isolation_level
            )
        else:  # Development mode with local PostgreSQL
            self.engine = sqlalchemy.create_engine(
                self.database_url,
                pool_size=5,
                max_overflow=2,
                pool_timeout=30,
                pool_recycle=1800,
                isolation_level=self.isolation_level
            )

        try:
            self.connection = self.engine.raw_connection()
            # Test the connection by getting a cursor
            self.get_cursor()
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
            transactions INTEGER NOT NULL DEFAULT 0 CHECK (transactions >= 0)
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

    def update_usage(self, account_id: int, prompt_tokens: int, completion_tokens: int):
        """Update token usage for an account with improved deadlock handling"""
        max_retries = 3
        retry_count = 0
        base_wait = 0.1  # 100ms base wait time
        
        while retry_count < max_retries:
            try:
                with self.get_transaction() as connection:
                    cursor = connection.get_cursor()
                    today = datetime.now().strftime('%Y-%m-%d')

                    # First update account_totals - always update this table first to maintain consistent lock order
                    cursor.execute(
                        self._get_sql('update_balance'),
                        (
                            prompt_tokens, completion_tokens,  # For account_totals update
                            account_id,  # For WHERE clause
                            prompt_tokens, completion_tokens,  # For balance check
                            account_id, today, prompt_tokens, completion_tokens  # For daily summary update
                        )
                    )
                    
                    # Check if update was successful
                    if cursor.rowcount == 0:
                        raise ValueError("Failed to update balance - insufficient tokens")

                    # Then update daily summary - always update this table second
                    cursor.execute(
                        self._get_sql('update_daily'),
                        (account_id, today, prompt_tokens, completion_tokens)
                    )
                    
                    # If we get here, the transaction succeeded
                    return
                    
            except Exception as e:
                if retry_count < max_retries - 1:
                    if "deadlock detected" in str(e):
                        retry_count += 1
                        wait_time = base_wait * (2 ** retry_count)  # Exponential backoff
                        time.sleep(wait_time)
                        logging.warning(f"Deadlock detected, retrying (attempt {retry_count + 1}/{max_retries})")
                        continue
                logging.error(f"Database error after {retry_count + 1} retries: {str(e)}")
                raise RuntimeError(f"CRITICAL: Failed to update token usage in database: {str(e)}")
            
        raise RuntimeError(f"Failed to update usage after {max_retries} retries due to database contention")

    def get_account_balance(self, account_id: int):
        """Get current token balance for an account"""
        connection = self._get_connection()
        cursor = connection.get_cursor()
        cursor.execute("""
        SELECT token_in, token_out, transactions 
        FROM account_totals 
        WHERE account_id = ?
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
        WHERE account_id = ? AND date BETWEEN ? AND ?
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
                    transactions INTEGER NOT NULL DEFAULT 0 CHECK (transactions >= 0)
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
        WHERE account_id = ?
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
        WHERE account_id = ? AND date BETWEEN ? AND ?
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

    def update_usage_with_response(self, account_id: int, prompt_tokens: int, completion_tokens: int) -> 'TokenUsageResponse':
        """Update token usage and return a TokenUsageResponse model"""
        from routellm.models import TokenUsageResponse, TokenUsageUpdate, AccountTokenBalance, InsufficientTokensError
        
        logging.info(f"=== DATABASE UPDATE START ===")
        logging.info(f"Account ID: {account_id}")
        logging.info(f"Prompt Tokens: {prompt_tokens}")
        logging.info(f"Completion Tokens: {completion_tokens}")
        
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
                logging.info(f"Current Balance: {current_balance}")
                
                # Try to update the balance
                cursor.execute(
                    self._get_sql('update_balance'),
                    (
                        prompt_tokens, completion_tokens,
                        account_id,
                        prompt_tokens, completion_tokens,
                        account_id, datetime.now().strftime('%Y-%m-%d'), prompt_tokens, completion_tokens  # Add missing parameters
                    )
                )
                
                result = cursor.fetchone()
                if not result:
                    # If update failed, it means insufficient balance
                    logging.error(f"Insufficient balance update failed. Current: {current_balance}, Required: in={prompt_tokens}, out={completion_tokens}")
                    return TokenUsageResponse(
                        account_id=account_id,
                        error=InsufficientTokensError(
                            message="Insufficient token balance",
                            current_balance=AccountTokenBalance(
                                account_id=account_id,
                                token_in=float(current_balance['token_in']),
                                token_out=float(current_balance['token_out']),
                                transactions=int(current_balance['transactions'])
                            ),
                            required_tokens=TokenUsageUpdate(
                                account_id=account_id,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens
                            )
                        )
                    )
                
                new_balance = {
                    "token_in": float(result[0]),
                    "token_out": float(result[1]),
                    "transactions": int(result[2])
                }
                logging.info(f"New Balance After Update: {new_balance}")
                
                # No need for separate daily summary update since it's handled in the combined query
                logging.info("=== DATABASE UPDATE END ===")
                
                # Return success response with new balance
                return TokenUsageResponse(
                    account_id=account_id,
                    new_balance=AccountTokenBalance(
                        account_id=account_id,
                        token_in=float(result[0]),
                        token_out=float(result[1]),
                        transactions=int(result[2])
                    ),
                    usage=TokenUsageUpdate(
                        account_id=account_id,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens
                    )
                )
                
        except Exception as e:
            logging.error(f"Error updating usage: {str(e)}")
            raise RuntimeError(f"Failed to update token usage: {str(e)}")