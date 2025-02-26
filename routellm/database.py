import os
from datetime import datetime
import logging
import threading
from contextlib import contextmanager
import time
from urllib.parse import urlparse
import random
import socket
import subprocess
from typing import Optional, Tuple, Dict, Any, List
from sqlalchemy import event, exc, create_engine
from sqlalchemy.sql import text
from sqlalchemy.pool import QueuePool
from sqlalchemy.engine import Engine

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

# Constants for connection management
DEFAULT_POOL_SIZE = 10
DEFAULT_MAX_OVERFLOW = 5
DEFAULT_POOL_TIMEOUT = 30
DEFAULT_POOL_RECYCLE = 300  # 5 minutes instead of 30 minutes
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 0.1
DEFAULT_CONNECT_TIMEOUT = 5  # seconds
DEFAULT_COMMAND_TIMEOUT = 10  # seconds
DEFAULT_VALIDATION_INTERVAL = 60  # seconds

class DatabaseConnection:
    def __init__(self):
        self.connection = None
        self.cursor = None
        self.engine = None
        self.isolation_level = "READ COMMITTED"
        self._in_transaction = False
        self._deadlock_retries = 3
        self._deadlock_wait = 0.1  # Initial wait time in seconds
        self._last_validation_time = 0
        self._validation_interval = DEFAULT_VALIDATION_INTERVAL

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
    
    def validate(self) -> bool:
        """Validate the connection is still working"""
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
        self.pool_size = int(os.getenv("DB_POOL_SIZE", str(DEFAULT_POOL_SIZE)))
        self.max_overflow = int(os.getenv("DB_MAX_OVERFLOW", str(DEFAULT_MAX_OVERFLOW)))
        self.pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", str(DEFAULT_POOL_TIMEOUT)))
        self.pool_recycle = int(os.getenv("DB_POOL_RECYCLE", str(DEFAULT_POOL_RECYCLE)))
        self.max_retries = int(os.getenv("DB_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))
        self.retry_backoff = float(os.getenv("DB_RETRY_BACKOFF", str(DEFAULT_RETRY_BACKOFF)))
        self.connect_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", str(DEFAULT_CONNECT_TIMEOUT)))
        self.command_timeout = int(os.getenv("DB_COMMAND_TIMEOUT", str(DEFAULT_COMMAND_TIMEOUT)))
        
        if instance_connection_name:
            # Parse credentials from DATABASE_URL for Cloud SQL
            parsed = urlparse(database_url)
            self.db_user = parsed.username
            self.db_pass = parsed.password
            self.db_name = parsed.path.lstrip('/')
        else:
            # Parse credentials for local development
            parsed = urlparse(database_url)
            self.db_host = parsed.hostname
            self.db_port = parsed.port
            self.db_user = parsed.username
            self.db_pass = parsed.password
            self.db_name = parsed.path.lstrip('/')

        self._local = threading.local()  # Thread-local storage
        
    def _set_last_validation_time(self, value):
        """Thread-safe setter for last validation time"""
        self._local.last_validation_time = value
        
    def _get_last_validation_time(self):
        """Thread-safe getter for last validation time"""
        if not hasattr(self._local, 'last_validation_time'):
            self._local.last_validation_time = 0
        return self._local.last_validation_time

    def connect(self):
        """Establish a connection to the database with proper error handling and logging"""
        start_time = time.time()
        env = os.getenv("ENVIRONMENT", "dev")
        logging.info(f"Creating new database connection in {env} environment")
        
        if self.instance_connection_name:  # Production mode with Cloud SQL
            logging.info(f"Connecting to Cloud SQL instance: {self.instance_connection_name}")
            connector = Connector(refresh_strategy="BACKGROUND")  # Changed from ALWAYS to BACKGROUND
            
            def getconn():
                try:
                    logging.info(f"Establishing connection to Cloud SQL")
                    conn = connector.connect(
                        self.instance_connection_name,
                        "pg8000",
                        user=self.db_user,
                        password=self.db_pass,
                        db=self.db_name,
                        ip_type=IPTypes.PRIVATE if self.private_ip else IPTypes.PUBLIC,
                        timeout=self.connect_timeout
                    )
                    # Set autocommit temporarily to configure session
                    conn.autocommit = True
                    cursor = conn.cursor()
                    cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED")
                    cursor.execute(f"SET lock_timeout = '{self.command_timeout}s'")
                    cursor.execute(f"SET statement_timeout = '{self.command_timeout}s'")
                    cursor.execute("SET idle_in_transaction_session_timeout = '60s'")  # 1 minute timeout for idle transactions
                    # Restore autocommit to False for normal operations
                    conn.autocommit = False
                    logging.info(f"Successfully connected to Cloud SQL in {time.time() - start_time:.2f}s")
                    return conn
                except Exception as e:
                    error_type = type(e).__name__
                    logging.error(f"Failed to connect to Cloud SQL: {error_type}: {str(e)}")
                    
                    # Log additional diagnostic information
                    self._log_connection_diagnostics()
                    
                    raise

            # Create engine with optimized settings
            self.engine = create_engine(
                "postgresql+pg8000://",
                creator=getconn,
                pool_size=self.pool_size,
                max_overflow=self.max_overflow,
                pool_timeout=self.pool_timeout,
                pool_recycle=self.pool_recycle,
                pool_pre_ping=True,
                isolation_level="READ COMMITTED",
                connect_args={"timeout": self.connect_timeout},
                echo=os.getenv("SQL_DEBUG", "").lower() in ("true", "1", "yes")
            )
        else:  # Development mode with local PostgreSQL
            logging.info(f"Connecting to local PostgreSQL at {self.db_host}:{self.db_port}")
            self.engine = create_engine(
                self.database_url,
                pool_size=self.pool_size,
                max_overflow=self.max_overflow,
                pool_timeout=self.pool_timeout,
                pool_recycle=self.pool_recycle,
                pool_pre_ping=True,
                isolation_level="READ COMMITTED",
                connect_args={"timeout": self.connect_timeout},
                echo=os.getenv("SQL_DEBUG", "").lower() in ("true", "1", "yes")
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
            )) or "network" in str(error).lower()
            
            if is_disconnect:
                logging.warning(f"Database connection error detected: {type(error).__name__}: {str(error)}")
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
            logging.info("Obtaining raw connection from engine")
            self.connection = self.engine.raw_connection()
            # Set autocommit temporarily to configure session
            self.connection.autocommit = True
            cursor = self.get_cursor()
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cursor.execute(f"SET lock_timeout = '{self.command_timeout}s'")
            cursor.execute(f"SET statement_timeout = '{self.command_timeout}s'")
            cursor.execute("SET idle_in_transaction_session_timeout = '60s'")  # 1 minute timeout for idle transactions
            # Restore autocommit to False for normal operations
            self.connection.autocommit = False
            
            # Validate the connection
            self.validate()
            
            logging.info(f"Database connection established successfully in {time.time() - start_time:.2f}s")
            return self.connection
        except Exception as e:
            error_type = type(e).__name__
            logging.error(f"Failed to establish database connection: {error_type}: {str(e)}")
            
            # Log additional diagnostic information
            self._log_connection_diagnostics()
            
            # Clean up resources
            if self.connection:
                try:
                    self.connection.close()
                except Exception:
                    pass
                self.connection = None
            
            if self.engine:
                try:
                    self.engine.dispose()
                except Exception:
                    pass
                self.engine = None
                
            raise RuntimeError(f"Failed to establish database connection: {str(e)}")
    
    def _log_connection_diagnostics(self):
        """Log diagnostic information for connection issues"""
        try:
            # Log environment information
            env = os.getenv("ENVIRONMENT", "Not set")
            instance_name = os.getenv("INSTANCE_CONNECTION_NAME", "Not set")
            logging.error(f"Database connection environment: ENVIRONMENT={env}, INSTANCE_CONNECTION_NAME={instance_name}")
            
            # Check if Cloud SQL Proxy is running
            if env == "prod" or instance_name != "Not set":
                try:
                    result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
                    proxy_running = "cloud-sql-proxy" in result.stdout
                    logging.error(f"Cloud SQL Proxy running: {proxy_running}")
                    
                    # Check if port 5432 is open
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(('127.0.0.1', 5432))
                    port_open = (result == 0)
                    sock.close()
                    logging.error(f"Port 5432 is {'open' if port_open else 'closed'}")
                except Exception as e:
                    logging.error(f"Error checking Cloud SQL Proxy: {str(e)}")
        except Exception as e:
            logging.error(f"Error during connection diagnostics: {str(e)}")

    def get_cursor(self):
        """Get a cursor from the current connection"""
        if not self.connection:
            raise RuntimeError("No connection established. Call connect() first.")
        if not self.cursor:
            self.cursor = self.connection.cursor()
        return self.cursor

    def begin_transaction(self):
        """Begin a new transaction if not already in one"""
        if not self._in_transaction:
            cursor = self.get_cursor()
            cursor.execute("BEGIN TRANSACTION")
            self._in_transaction = True
            logging.debug("Transaction started")

    def commit(self):
        """Commit the current transaction if one exists"""
        if self._in_transaction:
            self.connection.commit()
            self._in_transaction = False
            logging.debug("Transaction committed")

    def rollback(self):
        """Rollback the current transaction if one exists"""
        if self._in_transaction:
            try:
                self.connection.rollback()
                logging.debug("Transaction rolled back")
            except Exception as e:
                logging.error(f"Error during rollback: {type(e).__name__}: {str(e)}")
                # If rollback fails, the connection is likely in a bad state
                self.invalidate()
            finally:
                self._in_transaction = False

    def close(self):
        """Close the connection and release resources"""
        logging.info("Closing database connection")
        if self.cursor:
            try:
                self.cursor.close()
            except Exception as e:
                logging.warning(f"Error closing cursor: {str(e)}")
            self.cursor = None
            
        if self.connection:
            try:
                if self._in_transaction:
                    try:
                        self.connection.rollback()
                    except Exception:
                        pass
                    self._in_transaction = False
                self.connection.close()
            except Exception as e:
                logging.warning(f"Error closing connection: {str(e)}")
            self.connection = None
            
        if self.engine:
            try:
                self.engine.dispose()
            except Exception as e:
                logging.warning(f"Error disposing engine: {str(e)}")
            self.engine = None
        
        logging.info("Database connection closed")
    
    def validate(self) -> bool:
        """Validate the connection is still alive
        
        Returns:
            bool: True if connection is valid, False otherwise
        """
        # Skip validation if we've validated recently
        current_time = time.time()
        last_validation = self._get_last_validation_time()
        
        if current_time - last_validation < DEFAULT_VALIDATION_INTERVAL:
            return True
            
        if not self.connection:
            logging.warning("Connection validation failed: Not connected")
            return False
            
        try:
            # Execute a simple query to check connection
            cursor = self.get_cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            
            is_valid = result is not None and result[0] == 1
            if is_valid:
                self._set_last_validation_time(current_time)
                return True
            else:
                logging.warning("Connection validation failed: unexpected result")
                return False
        except Exception as e:
            logging.warning(f"Connection validation failed: {str(e)}")
            self._is_connected = False
            return False
    
    def invalidate(self):
        """Mark the connection as invalid and force recreation"""
        logging.info("Invalidating database connection")
        self.close()

class Database:
    # SQL Templates standardized for PostgreSQL
    SQL_TEMPLATES = {
        'upsert_account': """
            INSERT INTO account_totals (account_id)
            VALUES (%s)
            ON CONFLICT (account_id) DO UPDATE SET
                account_id = account_totals.account_id
            RETURNING token_balance_in, token_balance_out, transactions
        """,
        'update_balance': """
            WITH updated_totals AS (
                UPDATE account_totals SET
                    token_balance_in = token_balance_in - %s,
                    token_balance_out = token_balance_out - %s,
                    transactions = transactions + 1
                WHERE account_id = %s
                    AND token_balance_in >= %s 
                    AND token_balance_out >= %s
                RETURNING token_balance_in, token_balance_out, transactions
            ),
            daily_update AS (
                INSERT INTO account_daily_summary (account_id, date, daily_token_usage_in, daily_token_usage_out, transaction_count)
                VALUES (%s, %s::date, %s, %s, 1)
                ON CONFLICT(account_id, date) DO UPDATE SET
                    transaction_count = account_daily_summary.transaction_count + 1,
                    daily_token_usage_in = account_daily_summary.daily_token_usage_in + EXCLUDED.daily_token_usage_in,
                    daily_token_usage_out = account_daily_summary.daily_token_usage_out + EXCLUDED.daily_token_usage_out
            )
            SELECT * FROM updated_totals
        """,
        'update_daily': """
            INSERT INTO account_daily_summary 
                (account_id, date, daily_token_usage_in, daily_token_usage_out, transaction_count)
            VALUES (%s, %s::date, %s, %s, 1)
            ON CONFLICT(account_id, date) DO UPDATE SET
                transaction_count = COALESCE(account_daily_summary.transaction_count, 0) + 1,
                daily_token_usage_in = account_daily_summary.daily_token_usage_in + EXCLUDED.daily_token_usage_in,
                daily_token_usage_out = account_daily_summary.daily_token_usage_out + EXCLUDED.daily_token_usage_out
        """,
        'check_balance': """
            SELECT token_balance_in, token_balance_out, transactions
            FROM account_totals
            WHERE account_id = %s;
        """
    }

    def __init__(self):
        self.env = os.getenv("ENVIRONMENT", "dev")
        self._local = threading.local()
        self.param_style = "%s"  # Always use PostgreSQL style
        self._update_lock = threading.Lock()  # Add global lock for updates
        self._validation_interval = int(os.getenv("DB_VALIDATION_INTERVAL", str(DEFAULT_VALIDATION_INTERVAL)))
        logging.info(f"Initializing database in {'development' if self.env == 'dev' else 'production'} environment")
        
        # Don't initialize the database connection here - do it lazily
        # This prevents issues with connections being created in the main thread
        # and then accessed from worker threads
    
    def _get_connection(self, force_new=False, recursion_depth=0):
        """Get thread-local connection with proper thread safety settings
        
        Args:
            force_new: If True, force creation of a new connection
            recursion_depth: Current recursion depth to prevent stack overflow
        """
        # Prevent excessive recursion
        max_recursion = 3
        if recursion_depth >= max_recursion:
            logging.error(f"Maximum recursion depth ({max_recursion}) reached in _get_connection")
            raise RuntimeError(f"Failed to establish a valid database connection after {max_recursion} attempts")
            
        # If force_new is True or we don't have a connection yet, create a new one
        if force_new or not hasattr(self._local, 'db'):
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL environment variable is required")
                
            # Get environment-specific configuration
            instance_connection_name = os.getenv("INSTANCE_CONNECTION_NAME")
            private_ip = bool(os.getenv("PRIVATE_IP"))

            # If we already have a connection, close it properly
            if hasattr(self._local, 'db'):
                try:
                    self._local.db.close()
                except Exception as e:
                    logging.warning(f"Error closing existing connection: {str(e)}")

            try:
                logging.info("Creating new database connection")
                self._local.db = PostgreSQLConnection(
                    database_url=database_url,
                    instance_connection_name=instance_connection_name,
                    private_ip=private_ip
                )
                self._local.db.connect()
                self._ensure_tables_exist(self._local.db)
                self._local.last_validation = time.time()
            except Exception as e:
                error_msg = f"Failed to initialize database connection: {type(e).__name__}: {str(e)}"
                logging.error(error_msg)
                if hasattr(self._local, 'db'):
                    delattr(self._local, 'db')
                raise RuntimeError(error_msg)

        # Validate the connection periodically
        current_time = time.time()
        if not hasattr(self._local, 'last_validation') or \
           current_time - self._local.last_validation > self._validation_interval:
            try:
                if not self._local.db.validate():
                    logging.warning("Connection validation failed, creating new connection")
                    return self._get_connection(force_new=True, recursion_depth=recursion_depth+1)
                self._local.last_validation = current_time
            except Exception as e:
                logging.warning(f"Error during connection validation: {str(e)}")
                return self._get_connection(force_new=True, recursion_depth=recursion_depth+1)

        return self._local.db
    
    def get_validated_connection(self):
        """Get a validated database connection, creating a new one if necessary"""
        max_attempts = 3
        attempt = 0
        
        while attempt < max_attempts:
            try:
                conn = self._get_connection()
                # Quick validation
                cursor = conn.get_cursor()
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                if result and result[0] == 1:
                    return conn
                else:
                    logging.warning("Connection validation failed: unexpected result")
            except Exception as e:
                logging.warning(f"Connection validation failed (attempt {attempt+1}/{max_attempts}): {type(e).__name__}: {str(e)}")
            
            # Force a new connection on the next attempt
            attempt += 1
            if attempt < max_attempts:
                try:
                    if hasattr(self._local, 'db'):
                        self._local.db.close()
                        delattr(self._local, 'db')
                except Exception:
                    pass
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
        
        # If we get here, we've failed all validation attempts
        raise RuntimeError("Failed to obtain a valid database connection after multiple attempts")

    def initialize_database(self):
        """Initialize database connection based on environment"""
        try:
            connection = self.get_validated_connection()
            
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
            logging.error(f"Failed to verify database write access: {type(e).__name__}: {str(e)}")
            raise RuntimeError(f"Database initialization failed - could not write to database: {str(e)}")

    @contextmanager
    def get_transaction(self):
        """Get a transaction context manager for safe database operations with improved error handling"""
        db = self.get_validated_connection()
        transaction_id = f"txn-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
        logging.debug(f"Starting transaction {transaction_id}")
        
        try:
            db.begin_transaction()
            yield db
            db.commit()
            logging.debug(f"Transaction {transaction_id} committed successfully")
        except Exception as e:
            logging.error(f"Transaction {transaction_id} failed: {type(e).__name__}: {str(e)}")
            
            try:
                db.rollback()
                logging.debug(f"Transaction {transaction_id} rolled back")
            except Exception as rollback_error:
                logging.error(f"Error during rollback of transaction {transaction_id}: {type(rollback_error).__name__}: {str(rollback_error)}")
                # If rollback fails, the connection is likely in a bad state
                if hasattr(self._local, 'db'):
                    try:
                        self._local.db.invalidate()
                        delattr(self._local, 'db')
                    except Exception:
                        pass
            
            error_type = type(e).__name__
            error_details = str(e)
            
            # Specifically target network errors for detailed logging
            if "network" in error_details.lower() or isinstance(e, (exc.DisconnectionError, exc.OperationalError, exc.TimeoutError)):
                # Log the exact error type and representation
                logging.error(f"Transaction {transaction_id} network error - Type: {error_type}, Repr: {repr(e)}")
                
                # Try to get connection state information
                try:
                    if hasattr(db, 'connection') and db.connection:
                        conn_info = "Connection exists"
                    else:
                        conn_info = "No connection"
                    logging.error(f"Connection state for transaction {transaction_id}: {conn_info}")
                except Exception:
                    pass
                
                # Log environment information
                env = os.getenv("ENVIRONMENT", "Not set")
                instance_name = os.getenv("INSTANCE_CONNECTION_NAME", "Not set")
                logging.error(f"Environment context for transaction {transaction_id}: ENVIRONMENT={env}, INSTANCE_CONNECTION_NAME={instance_name}")
                
                # Check if Cloud SQL Proxy is running
                try:
                    import subprocess
                    result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
                    proxy_running = "cloud-sql-proxy" in result.stdout
                    logging.error(f"Cloud SQL Proxy running for transaction {transaction_id}: {proxy_running}")
                except Exception:
                    pass
                
                # Force connection reset on next access
                if hasattr(self._local, 'db'):
                    try:
                        self._local.db.invalidate()
                        delattr(self._local, 'db')
                    except Exception:
                        pass
            
            raise

    def close(self):
        """Close all thread-local database connections"""
        if hasattr(self._local, 'db'):
            try:
                self._local.db.close()
            except Exception as e:
                logging.warning(f"Error closing database connection: {str(e)}")
            delattr(self._local, 'db')
        logging.info("Database connections closed")

    def _ensure_tables_exist(self, connection):
        """Ensure necessary tables exist without dropping existing ones"""
        cursor = connection.get_cursor()
        
        # Create account_totals table if it doesn't exist
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_totals (
            account_id INTEGER PRIMARY KEY,
            token_balance_in NUMERIC(18,6) NOT NULL DEFAULT 3000000 CHECK (token_balance_in >= 0),
            token_balance_out NUMERIC(18,6) NOT NULL DEFAULT 1000000 CHECK (token_balance_out >= 0),
            word_balance INTEGER NOT NULL DEFAULT 10000 CHECK (word_balance >= 0),
            total_token_usage_in NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (total_token_usage_in >= 0),
            total_token_usage_out NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (total_token_usage_out >= 0),
            total_word_usage INTEGER NOT NULL DEFAULT 0 CHECK (total_word_usage >= 0),
            transactions INTEGER NOT NULL DEFAULT 0 CHECK (transactions >= 0)
        )
        """)

        # Create account_daily_summary table if it doesn't exist
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_daily_summary (
            account_id INTEGER NOT NULL REFERENCES account_totals(account_id),
            date DATE NOT NULL,
            transaction_count INTEGER NOT NULL DEFAULT 0 CHECK (transaction_count >= 0),
            daily_token_usage_in NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (daily_token_usage_in >= 0),
            daily_token_usage_out NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (daily_token_usage_out >= 0),
            daily_word_usage INTEGER NOT NULL DEFAULT 0 CHECK (daily_word_usage >= 0),
            PRIMARY KEY (account_id, date)
        )
        """)
        
        # Create indices for better performance
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_summary_date 
        ON account_daily_summary(date)
        """)
        
        if self.env == "dev":
            connection.commit()

    def _get_sql(self, template_name: str) -> str:
        """Get SQL query with correct parameter style for current environment."""
        return self.SQL_TEMPLATES[template_name].format(
            placeholder=self.param_style
        ).strip()

    def update_usage_with_response(self, account_id: int, prompt_tokens: int, completion_tokens: int, word_count: int = 0) -> None:
        """Update account usage with response data.
        
        Args:
            account_id: The account ID to update
            prompt_tokens: The number of prompt tokens used
            completion_tokens: The number of completion tokens used
            word_count: The number of words generated (optional)
        """
        transaction_id = f"txn-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
        logging.info(f"=== DATABASE UPDATE START [ID: {transaction_id}] ===")
        logging.info(f"Account ID: {account_id}")
        logging.info(f"Prompt Tokens: {prompt_tokens}")
        logging.info(f"Completion Tokens: {completion_tokens}")
        logging.info(f"Word Count: {word_count}")
        
        # Use a longer timeout for this operation since it's critical
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                with self.get_transaction() as db:
                    cursor = db.get_cursor()
                    
                    # Set a longer timeout for this specific operation
                    cursor.execute("SET statement_timeout = '30s'")
                    
                    # Get current balance first
                    cursor.execute("""
                        SELECT token_balance_in, token_balance_out, word_balance, transactions,
                               total_token_usage_in, total_token_usage_out, total_word_usage
                        FROM account_totals
                        WHERE account_id = %s
                    """, (account_id,))
                    
                    result = cursor.fetchone()
                    if not result:
                        # Create account if it doesn't exist
                        cursor.execute("""
                            INSERT INTO account_totals (account_id, token_balance_in, token_balance_out, word_balance, 
                                                       transactions, total_token_usage_in, total_token_usage_out, total_word_usage)
                            VALUES (%s, %s, %s, %s, 0, 0, 0, 0)
                        """, (account_id, self.DEFAULT_TOKEN_BALANCE, self.DEFAULT_TOKEN_BALANCE, self.DEFAULT_WORD_BALANCE))
                        
                        current_balance = {
                            'token_balance_in': self.DEFAULT_TOKEN_BALANCE,
                            'token_balance_out': self.DEFAULT_TOKEN_BALANCE,
                            'word_balance': self.DEFAULT_WORD_BALANCE,
                            'transactions': 0,
                            'total_token_usage_in': 0,
                            'total_token_usage_out': 0,
                            'total_word_usage': 0
                        }
                    else:
                        current_balance = {
                            'token_balance_in': float(result[0]),
                            'token_balance_out': float(result[1]),
                            'word_balance': int(result[2]),
                            'transactions': int(result[3]),
                            'total_token_usage_in': float(result[4]),
                            'total_token_usage_out': float(result[5]),
                            'total_word_usage': int(result[6])
                        }
                    
                    logging.info(f"[ID: {transaction_id}] Current Balance: {current_balance}")
                    
                    # Update account totals
                    cursor.execute("""
                        UPDATE account_totals
                        SET token_balance_in = token_balance_in - %s,
                            token_balance_out = token_balance_out - %s,
                            word_balance = word_balance - %s,
                            transactions = transactions + 1,
                            total_token_usage_in = total_token_usage_in + %s,
                            total_token_usage_out = total_token_usage_out + %s,
                            total_word_usage = total_word_usage + %s
                        WHERE account_id = %s
                        RETURNING token_balance_in, token_balance_out, word_balance, transactions,
                                  total_token_usage_in, total_token_usage_out, total_word_usage
                    """, (prompt_tokens, completion_tokens, word_count, 
                          prompt_tokens, completion_tokens, word_count, account_id))
                    
                    updated_result = cursor.fetchone()
                    if not updated_result:
                        raise RuntimeError(f"Failed to update account totals for account {account_id}")
                    
                    # Insert daily usage record
                    today = datetime.now().strftime("%Y-%m-%d")
                    cursor.execute("""
                        INSERT INTO account_daily_summary 
                        (account_id, date, transaction_count, daily_token_usage_in, daily_token_usage_out, daily_word_usage)
                        VALUES (%s, %s, 1, %s, %s, %s)
                        ON CONFLICT (account_id, date) 
                        DO UPDATE SET
                            transaction_count = account_daily_summary.transaction_count + 1,
                            daily_token_usage_in = account_daily_summary.daily_token_usage_in + %s,
                            daily_token_usage_out = account_daily_summary.daily_token_usage_out + %s,
                            daily_word_usage = account_daily_summary.daily_word_usage + %s
                    """, (account_id, today, prompt_tokens, completion_tokens, word_count,
                          prompt_tokens, completion_tokens, word_count))
                    
                    # Get final balance
                    final_balance = {
                        'token_balance_in': float(updated_result[0]),
                        'token_balance_out': float(updated_result[1]),
                        'word_balance': int(updated_result[2]),
                        'transactions': int(updated_result[3]),
                        'total_token_usage_in': float(updated_result[4]),
                        'total_token_usage_out': float(updated_result[5]),
                        'total_word_usage': int(updated_result[6])
                    }
                    
                    logging.info(f"=== DATABASE UPDATE SUCCEEDED [ID: {transaction_id}] ===")
                    logging.info(f"[ID: {transaction_id}] Final Balance: {final_balance}")
                    
                    # Successfully updated, break out of retry loop
                    return
                    
            except Exception as e:
                retry_count += 1
                error_type = type(e).__name__
                error_msg = str(e)
                
                # Check if this is a timeout error
                is_timeout = "statement timeout" in error_msg.lower() or "57014" in error_msg
                
                if is_timeout and retry_count < max_retries:
                    # For timeout errors, wait and retry
                    logging.warning(f"[ID: {transaction_id}] Database update timed out (attempt {retry_count}/{max_retries}): {error_type}: {error_msg}")
                    time.sleep(1 * retry_count)  # Increasing backoff
                else:
                    # For other errors or if we've exhausted retries, raise the error
                    logging.error(f"[ID: {transaction_id}] CRITICAL: Failed to update token usage in database: {error_type}: {error_msg}")
                    raise RuntimeError(f"Failed to update token usage: {error_msg}") from e
        
        # If we've exhausted all retries
        if retry_count >= max_retries:
            error_msg = f"Failed to update token usage after {max_retries} attempts due to database timeouts"
            logging.error(f"[ID: {transaction_id}] CRITICAL: {error_msg}")
            raise RuntimeError(error_msg)

    def get_account_balance(self, account_id: int):
        """Get current token balance for an account"""
        connection = self._get_connection()
        cursor = connection.get_cursor()
        cursor.execute("""
        SELECT token_balance_in, token_balance_out, transactions 
        FROM account_totals 
        WHERE account_id = %s
        """, (account_id,))
        result = cursor.fetchone()
        if result:
            return {
                "token_balance_in": result[0],
                "token_balance_out": result[1],
                "transactions": result[2]
            }
        return None

    def get_daily_usage(self, account_id: int, start_date: str, end_date: str):
        """Get daily usage for an account within a date range"""
        connection = self._get_connection()
        cursor = connection.get_cursor()
        cursor.execute("""
        SELECT date, transaction_count, daily_token_usage_in, daily_token_usage_out, daily_word_usage 
        FROM account_daily_summary 
        WHERE account_id = %s AND date BETWEEN %s AND %s
        ORDER BY date
        """, (account_id, start_date, end_date))
        return cursor.fetchall()

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
                    token_balance_in NUMERIC(18,6) NOT NULL DEFAULT 3000000 CHECK (token_balance_in >= 0),
                    token_balance_out NUMERIC(18,6) NOT NULL DEFAULT 1000000 CHECK (token_balance_out >= 0),
                    word_balance INTEGER NOT NULL DEFAULT 10000 CHECK (word_balance >= 0),
                    total_token_usage_in NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (total_token_usage_in >= 0),
                    total_token_usage_out NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (total_token_usage_out >= 0),
                    total_word_usage INTEGER NOT NULL DEFAULT 0 CHECK (total_word_usage >= 0),
                    transactions INTEGER NOT NULL DEFAULT 0 CHECK (transactions >= 0)
                )
                """)

                # Create account_daily_summary table if it doesn't exist
                cursor.execute("""
                CREATE TABLE account_daily_summary (
                    account_id INTEGER NOT NULL REFERENCES account_totals(account_id),
                    date DATE NOT NULL,
                    transaction_count INTEGER NOT NULL DEFAULT 0 CHECK (transaction_count >= 0),
                    daily_token_usage_in NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (daily_token_usage_in >= 0),
                    daily_token_usage_out NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (daily_token_usage_out >= 0),
                    daily_word_usage INTEGER NOT NULL DEFAULT 0 CHECK (daily_word_usage >= 0),
                    PRIMARY KEY (account_id, date)
                )
                """)
                
                # Create indices for better query performance
                cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_summary_date 
                ON account_daily_summary(date)
                """)

        except Exception as e:
            logging.error(f"Error creating tables: {str(e)}")
            raise

    def check_sufficient_balance(self, account_id: int, prompt_tokens: int, completion_tokens: int, word_count: int = 0) -> tuple[bool, dict]:
        """Check if account has sufficient balance for the requested operation without updating the balance."""
        try:
            with self.get_transaction() as connection:
                cursor = connection.get_cursor()
                
                # First ensure account exists and get current balance
                cursor.execute("""
                    INSERT INTO account_totals (account_id)
                    VALUES (%s)
                    ON CONFLICT (account_id) DO UPDATE SET
                        account_id = account_totals.account_id
                    RETURNING token_balance_in, token_balance_out, word_balance, transactions
                """, (account_id,))
                result = cursor.fetchone()
                if not result:
                    raise RuntimeError("Failed to get or create account")
                
                current_balance = {
                    "token_balance_in": float(result[0]),
                    "token_balance_out": float(result[1]),
                    "word_balance": int(result[2]),
                    "transactions": int(result[3])
                }
                
                # Check if balance is sufficient for both tokens and words
                has_sufficient_balance = (
                    current_balance["token_balance_in"] >= prompt_tokens and 
                    current_balance["token_balance_out"] >= completion_tokens and
                    current_balance["word_balance"] >= word_count
                )
                
                return has_sufficient_balance, current_balance
                
        except Exception as e:
            error_type = type(e).__name__
            error_details = str(e)
            
            # Specifically target network errors for detailed logging
            if "network" in error_details.lower():
                # Log the exact error type and representation
                logging.error(f"Network error details - Type: {error_type}, Repr: {repr(e)}")
                
                # Log environment information
                env = os.getenv("ENVIRONMENT", "Not set")
                instance_name = os.getenv("INSTANCE_CONNECTION_NAME", "Not set")
                logging.error(f"Environment context - ENVIRONMENT: {env}, INSTANCE_CONNECTION_NAME: {instance_name}")
                
                # Check if Cloud SQL Proxy is running
                try:
                    import subprocess
                    result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
                    proxy_running = "cloud-sql-proxy" in result.stdout
                    logging.error(f"Cloud SQL Proxy running: {proxy_running}")
                except Exception:
                    pass
            
            logging.error(f"Error checking balance: {error_type}: {error_details}")
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
                        INSERT INTO account_totals (account_id, token_balance_in, token_balance_out, transactions)
                        VALUES (?, 3000000, 1000000, 0)
                        ON CONFLICT(account_id) DO UPDATE SET
                            token_balance_in = 3000000,
                            token_balance_out = 1000000,
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
        SELECT token_balance_in, token_balance_out, word_balance, transactions, 
               total_token_usage_in, total_token_usage_out, total_word_usage
        FROM account_totals 
        WHERE account_id = %s
        """, (account_id,))
        result = cursor.fetchone()
        
        if result:
            return AccountTokenBalance(
                account_id=account_id,
                token_balance_in=float(result[0]),
                token_balance_out=float(result[1]),
                word_balance=int(result[2]),
                transactions=int(result[3]),
                total_token_usage_in=float(result[4]),
                total_token_usage_out=float(result[5]),
                total_word_usage=int(result[6])
        )
        
        # If no result, create a new account with default values
        cursor.execute("""
            INSERT INTO account_totals (account_id)
            VALUES (%s)
            ON CONFLICT (account_id) DO UPDATE SET
                account_id = account_totals.account_id
            RETURNING token_balance_in, token_balance_out, word_balance, transactions,
                      total_token_usage_in, total_token_usage_out, total_word_usage
        """, (account_id,))
        result = cursor.fetchone()
        if not result:
            raise RuntimeError("Failed to get or create account balance")
            
        return AccountTokenBalance(
            account_id=account_id,
            token_balance_in=float(result[0]),
            token_balance_out=float(result[1]),
            word_balance=int(result[2]),
            transactions=int(result[3]),
            total_token_usage_in=float(result[4]),
            total_token_usage_out=float(result[5]),
            total_word_usage=int(result[6])
        )

    def get_daily_usage_model(self, account_id: int, start_date: str, end_date: str) -> list['DailyUsageSummary']:
        """Get daily usage for an account within a date range as Pydantic models"""
        from routellm.models import DailyUsageSummary
        
        connection = self._get_connection()
        cursor = connection.get_cursor()
        cursor.execute("""
        SELECT date, transaction_count, daily_token_usage_in, daily_token_usage_out, daily_word_usage 
        FROM account_daily_summary 
        WHERE account_id = %s AND date BETWEEN %s AND %s
        ORDER BY date
        """, (account_id, start_date, end_date))
        
        results = []
        for row in cursor.fetchall():
            # Convert date to string in YYYY-MM-DD format
            date_str = row[0].strftime("%Y-%m-%d") if hasattr(row[0], 'strftime') else str(row[0])
            
            results.append(DailyUsageSummary(
                account_id=account_id,
                date=date_str,
                transaction_count=int(row[1]),
                daily_token_usage_in=float(row[2]),
                daily_token_usage_out=float(row[3]),
                daily_word_usage=int(row[4])
            ))
        return results