import os
import sqlite3
from datetime import datetime
import logging
import threading
from contextlib import contextmanager

# First check environment and required dependencies
ENVIRONMENT = os.getenv("ENVIRONMENT")
GOOGLE_CLOUD_SQL_AVAILABLE = False  # Track if dependencies are available

if ENVIRONMENT != "dev":
    try:
        # Check all required production dependencies first
        from google.cloud.sql.connector import Connector, IPTypes
        import pg8000
        import sqlalchemy
        import google.cloud.logging
        from google.cloud.logging.handlers import CloudLoggingHandler, CloudLoggingFilter
        from google.cloud.logging_v2.handlers import setup_logging
        GOOGLE_CLOUD_SQL_AVAILABLE = True  # Mark dependencies as available

        # Set up Google Cloud Logging with proper severity mapping
        client = google.cloud.logging.Client()
        
        # Create handler with project ID for proper resource tracking
        handler = CloudLoggingHandler(
            client,
            name="python",  # This will show up as the logger name in Cloud Logging
        )
        
        # Add Cloud Logging filter to properly set project and add labels
        handler.addFilter(CloudLoggingFilter(
            project=client.project,
            default_labels={
                "environment": ENVIRONMENT,
                "application": "kavya",
                "service": "database"
            }
        ))
        
        # Configure the handler to use the correct severity mapping
        handler.setFormatter(logging.Formatter('%(message)s'))
        
        # Remove any existing handlers to avoid duplicate logging
        logging.getLogger().handlers = []
        
        # Add our configured handler
        logging.getLogger().addHandler(handler)
        
        # Set the logging level to INFO
        logging.getLogger().setLevel(logging.INFO)
        
        # Test the logging setup with different severity levels
        logging.info("Successfully configured Google Cloud Logging with severity mapping")
        
    except ImportError as e:
        # If any dependency is missing, log it clearly and exit
        missing_dep = str(e).split("'")[1] if "'" in str(e) else str(e)
        error_msg = f"Missing required production dependency: {missing_dep}"
        print(error_msg)  # Print because logging might not be set up
        raise ImportError(error_msg)
    except Exception as e:
        # For other errors (like Cloud Logging setup), fall back to basic logging
        print(f"Failed to setup Google Cloud Logging: {str(e)}")
        logging.basicConfig(level=logging.INFO)
else:
    # Development mode - use basic logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

class Database:
    # SQL Templates that work for both SQLite and PostgreSQL
    SQL_TEMPLATES = {
        'sqlite': {
            'check_balance': """
                INSERT INTO account_totals (account_id)
                VALUES ({placeholder})
                ON CONFLICT(account_id) DO UPDATE SET
                    token_in = token_in
                RETURNING token_in, token_out, transactions
            """,
            'update_balance': """
                UPDATE account_totals SET
                    token_in = CASE 
                        WHEN token_in >= {placeholder} THEN token_in - {placeholder}
                        ELSE token_in
                    END,
                    token_out = CASE 
                        WHEN token_out >= {placeholder} THEN token_out - {placeholder}
                        ELSE token_out
                    END,
                    transactions = CASE 
                        WHEN token_in >= {placeholder} AND token_out >= {placeholder}
                        THEN transactions + 1
                        ELSE transactions
                    END
                WHERE account_id = {placeholder}
                    AND token_in >= {placeholder} 
                    AND token_out >= {placeholder}
                RETURNING *
            """,
            'update_daily': """
                INSERT INTO account_daily_summary 
                    (account_id, date, transaction_count, token_in, token_out, last_updated)
                VALUES ({placeholder}, {placeholder}, 1, {placeholder}, {placeholder}, strftime('%Y-%m-%d %H:%M:%f', 'now'))
                ON CONFLICT(account_id, date) DO UPDATE SET
                    transaction_count = transaction_count + 1,
                    token_in = token_in + excluded.token_in,
                    token_out = token_out + excluded.token_out,
                    last_updated = strftime('%Y-%m-%d %H:%M:%f', 'now')
            """
        },
        'postgresql': {
            'check_balance': """
                INSERT INTO account_totals (account_id)
                VALUES ({placeholder})
                ON CONFLICT(account_id) DO UPDATE SET
                    token_in = account_totals.token_in
                RETURNING token_in, token_out, transactions
            """,
            'update_balance': """
                UPDATE account_totals SET
                    token_in = CASE 
                        WHEN token_in >= {placeholder} THEN token_in - {placeholder}
                        ELSE token_in
                    END,
                    token_out = CASE 
                        WHEN token_out >= {placeholder} THEN token_out - {placeholder}
                        ELSE token_out
                    END,
                    transactions = CASE 
                        WHEN token_in >= {placeholder} AND token_out >= {placeholder}
                        THEN transactions + 1
                        ELSE transactions
                    END
                WHERE account_id = {placeholder}
                    AND token_in >= {placeholder} 
                    AND token_out >= {placeholder}
                RETURNING *
            """,
            'update_daily': """
                INSERT INTO account_daily_summary 
                    (account_id, date, transaction_count, token_in, token_out, last_updated)
                VALUES ({placeholder}, {placeholder}::date, 1, {placeholder}, {placeholder}, CURRENT_TIMESTAMP)
                ON CONFLICT(account_id, date) DO UPDATE SET
                    transaction_count = account_daily_summary.transaction_count + 1,
                    token_in = account_daily_summary.token_in + EXCLUDED.token_in,
                    token_out = account_daily_summary.token_out + EXCLUDED.token_out,
                    last_updated = CURRENT_TIMESTAMP
            """
        }
    }

    def __init__(self):
        self.env = ENVIRONMENT
        self._local = threading.local()
        self.db_type = "sqlite" if self.env == "dev" else "postgresql"
        self.param_style = "?" if self.env == "dev" else "%s"
        logging.info(f"Initializing database in {'development' if self.env == 'dev' else 'production'} environment using {self.db_type}")
        self.initialize_database()

    def _get_connection(self):
        """Get thread-local connection with proper thread safety settings"""
        if not hasattr(self._local, 'connection'):
            if self.env == "dev":
                if sqlite3 is None:
                    raise ImportError("SQLite3 required for development environment")
                logging.info("Creating SQLite connection for development")
                db_path = os.path.join(os.path.dirname(__file__), "kavya.db")
                try:
                    # Test if we can create/write to the database file
                    if not os.path.exists(db_path):
                        open(db_path, 'w').close()
                    elif not os.access(db_path, os.W_OK):
                        raise Exception(f"Database file {db_path} exists but is not writable")
                    
                    # Use check_same_thread=False to allow cross-thread usage with thread-local storage
                    self._local.connection = sqlite3.connect(
                        db_path,
                        check_same_thread=False,
                        isolation_level=None  # This enables autocommit mode
                    )
                    
                    # Enable WAL mode for better concurrency
                    cursor = self._local.connection.cursor()
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.close()
                    
                except Exception as e:
                    raise Exception(f"Failed to initialize SQLite database: {str(e)}")
            else:
                if not GOOGLE_CLOUD_SQL_AVAILABLE:
                    raise ImportError("Google Cloud SQL dependencies required for production environment")
                
                db_socket_dir = os.getenv("DB_SOCKET_DIR")
                if not db_socket_dir:
                    raise ValueError("DB_SOCKET_DIR environment variable is required in production")
                    
                instance_connection_name = os.getenv("INSTANCE_CONNECTION_NAME")
                if not instance_connection_name:
                    raise ValueError("INSTANCE_CONNECTION_NAME environment variable is required in production")
                
                db_user = os.getenv("DB_USER")
                if not db_user:
                    raise ValueError("DB_USER environment variable is required in production")
                    
                db_pass = os.getenv("DB_PASS")
                if not db_pass:
                    raise ValueError("DB_PASS environment variable is required in production")
                    
                db_name = os.getenv("DB_NAME")
                if not db_name:
                    raise ValueError("DB_NAME environment variable is required in production")

                try:
                    # Initialize Cloud SQL Python Connector object
                    connector = Connector(refresh_strategy="LAZY")

                    def getconn():
                        conn: pg8000.dbapi.Connection = connector.connect(
                            instance_connection_name,
                            "pg8000",
                            user=db_user,
                            password=db_pass,
                            db=db_name,
                            ip_type=IPTypes.PRIVATE if os.getenv("PRIVATE_IP") else IPTypes.PUBLIC,
                        )
                        return conn

                    # The Cloud SQL Python Connector can be used with SQLAlchemy
                    pool = sqlalchemy.create_engine(
                        "postgresql+pg8000://",
                        creator=getconn,
                        # Pool size is the maximum number of permanent connections to keep.
                        pool_size=5,
                        # Temporarily exceeds the set pool_size if no connections are available.
                        max_overflow=2,
                        # The total number of concurrent connections for your application will be
                        # a total of pool_size and max_overflow.
                        pool_timeout=30,  # 30 seconds
                        pool_recycle=1800,  # 30 minutes
                    )
                    self._local.connection = pool.connect()
                except Exception as e:
                    raise Exception(f"Failed to connect to Cloud SQL PostgreSQL: {str(e)}")
            
            # Initialize tables if they don't exist
            self._ensure_tables_exist(self._local.connection)
        return self._local.connection

    def initialize_database(self):
        """Initialize database connection based on environment"""
        connection = self._get_connection()
        
        # Test that we can actually write to the database
        try:
            cursor = connection.cursor()
            # Try to insert and immediately delete a test record
            cursor.execute(
                self._get_sql('check_balance'),
                (-999,)  # Use a special test ID that won't conflict with real accounts
            )
            if self.env == "dev":
                connection.commit()
            logging.info("Successfully verified database write access")
        except Exception as e:
            logging.error("Failed to verify database write access")
            raise Exception(f"Database initialization failed - could not write to database: {str(e)}")

    def _ensure_tables_exist(self, connection):
        """Ensure necessary tables exist without dropping existing ones"""
        cursor = connection.cursor()
        
        # Create account_totals table if it doesn't exist
        if self.db_type == "sqlite":
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_totals (
                account_id INTEGER PRIMARY KEY,
                token_in NUMERIC(18,6) NOT NULL DEFAULT 3000000,
                token_out NUMERIC(18,6) NOT NULL DEFAULT 1000000,
                transactions INTEGER NOT NULL DEFAULT 0,
                CHECK (token_in >= 0),
                CHECK (token_out >= 0)
            )
            """)
        else:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_totals (
                account_id INTEGER PRIMARY KEY,
                token_in NUMERIC(18,6) NOT NULL DEFAULT 3000000,
                token_out NUMERIC(18,6) NOT NULL DEFAULT 1000000,
                transactions INTEGER NOT NULL DEFAULT 0,
                CHECK (token_in >= 0),
                CHECK (token_out >= 0)
            )
            """)

        # Create account_daily_summary table if it doesn't exist
        if self.db_type == "sqlite":
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_daily_summary (
                account_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                transaction_count INTEGER NOT NULL DEFAULT 0,
                token_in NUMERIC(18,6) NOT NULL DEFAULT 0,
                token_out NUMERIC(18,6) NOT NULL DEFAULT 0,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, date),
                FOREIGN KEY (account_id) REFERENCES account_totals(account_id),
                CHECK (token_in >= 0),
                CHECK (token_out >= 0),
                CHECK (transaction_count >= 0)
            )
            """)
        else:
            # PostgreSQL version
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_daily_summary (
                account_id INTEGER NOT NULL,
                date DATE NOT NULL,
                transaction_count INTEGER NOT NULL DEFAULT 0,
                token_in NUMERIC(18,6) NOT NULL DEFAULT 0,
                token_out NUMERIC(18,6) NOT NULL DEFAULT 0,
                last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, date),
                FOREIGN KEY (account_id) REFERENCES account_totals(account_id),
                CHECK (token_in >= 0),
                CHECK (token_out >= 0),
                CHECK (transaction_count >= 0)
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
        conn = self._get_connection()
        try:
            if self.env == "dev":
                # For SQLite, explicitly start a transaction
                conn.execute("BEGIN IMMEDIATE")
            
            yield conn
            
            if self.env == "dev":
                conn.commit()
        except Exception as e:
            if self.env == "dev":
                try:
                    conn.rollback()
                except Exception as rollback_error:
                    logging.error(f"Error during rollback: {str(rollback_error)}")
            logging.error(f"Transaction failed: {str(e)}")
            raise

    def _get_sql(self, template_name: str) -> str:
        """Get SQL query with correct parameter style for current environment."""
        return self.SQL_TEMPLATES[self.db_type][template_name].format(
            placeholder=self.param_style
        ).strip()

    def update_usage(self, account_id: int, prompt_tokens: int, completion_tokens: int):
        """Update token usage for an account"""
        try:
            # First check if balance is sufficient
            has_balance, current_balance = self.check_sufficient_balance(account_id, prompt_tokens, completion_tokens)
            if not has_balance:
                error_msg = (
                    f"Insufficient token balance. Current balance: "
                    f"{current_balance['token_in']} input tokens, "
                    f"{current_balance['token_out']} output tokens. "
                    f"Required: {prompt_tokens} input tokens, "
                    f"{completion_tokens} output tokens."
                )
                logging.error(f"Account {account_id}: {error_msg}")
                raise ValueError(error_msg)
            
            with self.get_transaction() as connection:
                cursor = connection.cursor()
                today = datetime.now().strftime('%Y-%m-%d')

                # Update account_totals with a single atomic update that checks balance
                cursor.execute(
                    self._get_sql('update_balance'),
                    (
                        prompt_tokens, prompt_tokens,  # token_in CASE
                        completion_tokens, completion_tokens,  # token_out CASE
                        prompt_tokens, completion_tokens,  # transactions CASE
                        account_id,  # WHERE account_id = ?
                        prompt_tokens, completion_tokens  # AND token_in >= ? AND token_out >= ?
                    )
                )
                
                # Check if update was successful
                if self.db_type == "sqlite":
                    result = cursor.fetchone()
                    if not result:
                        raise ValueError("Failed to update balance - insufficient tokens")
                else:
                    if cursor.rowcount == 0:
                        raise ValueError("Failed to update balance - insufficient tokens")

                # Update daily summary
                cursor.execute(
                    self._get_sql('update_daily'),
                    (account_id, today, prompt_tokens, completion_tokens)
                )
                
        except Exception as e:
            logging.error(f"Error updating usage: {str(e)}")
            raise

    def get_account_balance(self, account_id: int):
        """Get current token balance for an account"""
        connection = self._get_connection()
        cursor = connection.cursor()
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
        cursor = connection.cursor()
        cursor.execute("""
        SELECT date, transaction_count, token_in, token_out 
        FROM account_daily_summary 
        WHERE account_id = ? AND date BETWEEN ? AND ?
        ORDER BY date
        """, (account_id, start_date, end_date))
        return cursor.fetchall()

    def close(self):
        """Close all thread-local database connections"""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()
            del self._local.connection 

    def create_tables(self):
        """Create the necessary tables if they don't exist"""
        try:
            with self.get_transaction() as connection:
                cursor = connection.cursor()
                
                # Drop existing tables to ensure clean schema
                cursor.execute("DROP TABLE IF EXISTS account_daily_summary")
                cursor.execute("DROP TABLE IF EXISTS account_totals")
                
                # Create account_totals table
                cursor.execute("""
                CREATE TABLE account_totals (
                    account_id INTEGER PRIMARY KEY,
                    token_in INTEGER NOT NULL DEFAULT 3000000,
                    token_out INTEGER NOT NULL DEFAULT 1000000,
                    transactions INTEGER NOT NULL DEFAULT 0,
                    CHECK (token_in >= 0),
                    CHECK (token_out >= 0)
                )
                """)

                # Create account_daily_summary table with composite primary key
                if self.env == "dev":
                    # SQLite version
                    cursor.execute("""
                    CREATE TABLE account_daily_summary (
                        account_id INTEGER NOT NULL,
                        date DATE NOT NULL,
                        transaction_count INTEGER NOT NULL DEFAULT 0,
                        token_in INTEGER NOT NULL DEFAULT 0,
                        token_out INTEGER NOT NULL DEFAULT 0,
                        last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (account_id, date),
                        FOREIGN KEY (account_id) REFERENCES account_totals(account_id),
                        CHECK (token_in >= 0),
                        CHECK (token_out >= 0),
                        CHECK (transaction_count >= 0)
                    )
                    """)
                else:
                    # Google Cloud SQL version
                    cursor.execute("""
                    CREATE TABLE account_daily_summary (
                        account_id INTEGER NOT NULL,
                        date DATE NOT NULL,
                        transaction_count INTEGER NOT NULL DEFAULT 0,
                        token_in INTEGER NOT NULL DEFAULT 0,
                        token_out INTEGER NOT NULL DEFAULT 0,
                        last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (account_id, date),
                        FOREIGN KEY (account_id) REFERENCES account_totals(account_id),
                        CHECK (token_in >= 0),
                        CHECK (token_out >= 0),
                        CHECK (transaction_count >= 0)
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
        """Check if account has sufficient balance for the requested operation."""
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            # Get current balance or default values if account doesn't exist
            cursor.execute(
                self._get_sql('check_balance'),
                (account_id,)
            )
            result = cursor.fetchone()
            if not result:
                raise RuntimeError("Failed to get balance after insert")

            current_balance = {
                "token_in": float(result[0]),
                "token_out": float(result[1]),
                "transactions": int(result[2])
            }
            
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
                cursor = connection.cursor()
                
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
            cursor = connection.cursor()
            
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
        cursor = connection.cursor()
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
            (account_id,)
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
        cursor = connection.cursor()
        cursor.execute("""
        SELECT date, transaction_count, token_in, token_out, last_updated
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
        
        try:
            # First check if balance is sufficient
            has_balance, current_balance = self.check_sufficient_balance(account_id, prompt_tokens, completion_tokens)
            if not has_balance:
                raise InsufficientTokensError(
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
            
            # Update the usage
            self.update_usage(account_id, prompt_tokens, completion_tokens)
            
            # Get the new balance
            new_balance = self.get_account_balance_model(account_id)
            if not new_balance:
                raise RuntimeError("Failed to get updated balance")
            
            # Create and return the response
            return TokenUsageResponse(
                account_id=account_id,
                new_balance=new_balance,
                usage=TokenUsageUpdate(
                    account_id=account_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens
                )
            )
        except InsufficientTokensError:
            raise
        except Exception as e:
            logging.error(f"Error updating usage: {str(e)}")
            raise