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
from sqlalchemy import event, exc, create_engine
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

# Configure basic logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Constants for connection management
DEFAULT_POOL_SIZE = 5
DEFAULT_MAX_OVERFLOW = 10
DEFAULT_POOL_TIMEOUT = 20000  # milliseconds (20 seconds)
DEFAULT_POOL_RECYCLE = 1800000  # milliseconds (30 minutes)
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_BACKOFF = 500  # milliseconds (0.5 seconds)
DEFAULT_CONNECT_TIMEOUT = 10000  # milliseconds (10 seconds)
DEFAULT_COMMAND_TIMEOUT = 20000  # milliseconds (20 seconds)
DEFAULT_LOCK_TIMEOUT = 20000  # milliseconds (20 seconds)
DEFAULT_STATEMENT_TIMEOUT = 20000  # milliseconds (20 seconds)
DEFAULT_VALIDATION_INTERVAL = 180000  # milliseconds (60 seconds)
# Add constants for retry handling specific to token updates
DEFAULT_TOKEN_UPDATE_RETRIES = 10
DEFAULT_TOKEN_UPDATE_BACKOFF_BASE = 200  # milliseconds (0.2 seconds)
# Add constants for fast operation timeouts
DEFAULT_FAST_LOCK_TIMEOUT = 250  # milliseconds
DEFAULT_FAST_STATEMENT_TIMEOUT = 1000  # milliseconds
# Add constants for restart recovery
DEFAULT_RESTART_DETECTION_WINDOW = 300000  # milliseconds (5 minutes)
DEFAULT_RESTART_BACKOFF_MULTIPLIER = 2.0
# Add constants for advisory locks
# https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS
PG_LOCK_NAMESPACE = 54321  # Custom namespace for our application's advisory locks
# Add constants for initialization
DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT = 20000  # milliseconds (20 seconds)

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
        self.lock_timeout = int(os.getenv("DB_LOCK_TIMEOUT", str(DEFAULT_LOCK_TIMEOUT)))
        self.statement_timeout = int(os.getenv("DB_STATEMENT_TIMEOUT", str(DEFAULT_STATEMENT_TIMEOUT)))
        self.token_update_retries = int(os.getenv("DB_TOKEN_UPDATE_RETRIES", str(DEFAULT_TOKEN_UPDATE_RETRIES)))
        self.token_update_backoff = float(os.getenv("DB_TOKEN_UPDATE_BACKOFF_BASE", str(DEFAULT_TOKEN_UPDATE_BACKOFF_BASE)))
        self.idle_in_transaction_timeout = int(os.getenv("DB_IDLE_IN_TRANSACTION_TIMEOUT", str(DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT)))
        
        # Add VACUUM optimization settings
        self.fillfactor_account_totals = int(os.getenv("DB_FILLFACTOR_ACCOUNT_TOTALS", "90"))
        self.fillfactor_account_daily = int(os.getenv("DB_FILLFACTOR_ACCOUNT_DAILY", "95"))
        self.autovacuum_enabled = os.getenv("DB_AUTOVACUUM_ENABLED", "true").lower() in ("true", "1", "yes")
        
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
                        timeout=self.connect_timeout / 1000,  # Convert from ms to seconds for pg8000
                    )
                    
                    # Skip setting timeouts as they're already configured as needed
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
                pool_timeout=self.pool_timeout / 1000,  # Convert from ms to seconds
                pool_recycle=self.pool_recycle / 1000,  # Convert from ms to seconds
                pool_pre_ping=True,
                isolation_level="READ COMMITTED",
                echo=os.getenv("SQL_DEBUG", "").lower() in ("true", "1", "yes")
            )
        else:  # Development mode with local PostgreSQL
            logging.info(f"Connecting to local PostgreSQL at {self.db_host}:{self.db_port}")
            
            # Define common connection options for local PostgreSQL
            connect_args = {
                "timeout": self.connect_timeout / 1000,  # Convert from ms to seconds for pg8000
            }
            
            self.engine = create_engine(
                self.database_url,
                pool_size=self.pool_size,
                max_overflow=self.max_overflow,
                pool_timeout=self.pool_timeout / 1000,  # Convert from ms to seconds
                pool_recycle=self.pool_recycle / 1000,  # Convert from ms to seconds
                pool_pre_ping=True,
                isolation_level="READ COMMITTED",
                connect_args=connect_args,
                echo=os.getenv("SQL_DEBUG", "").lower() in ("true", "1", "yes")
            )
            
            # For local connections, set timeouts on the first connection
            connection = self.engine.raw_connection()
            try:
                # Skip setting timeouts as they're already configured as needed
                pass
            finally:
                connection.close()

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

    def _ensure_tables_exist(self, connection):
        """Ensure all required tables exist in the database"""
        logging.info("Ensuring required tables exist")
        cursor = connection.get_cursor()
        
        # Skip setting timeouts as they're already configured as needed
        
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
            transactions INTEGER NOT NULL DEFAULT 0 CHECK (transactions >= 0),
            last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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
        
        # Create or update indices for better performance
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_account_totals_usage 
        ON account_totals(token_balance_in, token_balance_out, word_balance)
        """)
        
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_summary_date 
        ON account_daily_summary(date)
        """)
        
        # Update the account_totals table to include the last_updated column if it doesn't exist
        try:
            cursor.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name='account_totals' AND column_name='last_updated'
            """)
            has_last_updated = cursor.fetchone() is not None
            
            if not has_last_updated:
                cursor.execute("""
                ALTER TABLE account_totals ADD COLUMN last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                """)
                logging.info("Added last_updated column to account_totals table")
        except Exception as e:
            logging.warning(f"Error checking for last_updated column: {str(e)}")
        
        # Apply VACUUM optimizations
        self._optimize_table_storage(cursor)
        
        connection.commit()
        logging.info("Tables and indices verified")
        
    def _optimize_table_storage(self, cursor):
        """Apply VACUUM optimizations to tables"""
        try:
            # Set FILLFACTOR for account_totals (frequently updated)
            cursor.execute(f"""
                ALTER TABLE account_totals SET (fillfactor = {self.fillfactor_account_totals})
            """)
            
            # Set FILLFACTOR for account_daily_summary (less frequently updated)
            cursor.execute(f"""
                ALTER TABLE account_daily_summary SET (fillfactor = {self.fillfactor_account_daily})
            """)
            
            # Configure autovacuum settings if enabled
            if self.autovacuum_enabled:
                # For account_totals (frequently updated, needs more aggressive vacuuming)
                cursor.execute("""
                    ALTER TABLE account_totals SET (
                        autovacuum_vacuum_scale_factor = 0.05,
                        autovacuum_analyze_scale_factor = 0.02,
                        autovacuum_vacuum_threshold = 50,
                        autovacuum_analyze_threshold = 50
                    )
                """)
                
                # For account_daily_summary (append-mostly table)
                cursor.execute("""
                    ALTER TABLE account_daily_summary SET (
                        autovacuum_vacuum_scale_factor = 0.1,
                        autovacuum_analyze_scale_factor = 0.05,
                        autovacuum_vacuum_threshold = 100,
                        autovacuum_analyze_threshold = 100
                    )
                """)
            
            logging.info("Table storage optimized with VACUUM settings")
        except Exception as e:
            logging.warning(f"Error applying VACUUM optimizations: {str(e)}")

class Database:
    # Default balance values
    DEFAULT_TOKEN_BALANCE_IN = 3000000
    DEFAULT_TOKEN_BALANCE_OUT = 1000000
    DEFAULT_WORD_BALANCE = 10000

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
                if conn.validate():
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
            # Use a more resilient approach with shorter timeouts
            with self.get_transaction() as db:
                cursor = db.get_cursor()
                
                # First try a simple read query to verify basic connectivity
                cursor.execute("SELECT 1 as test")
                if cursor.fetchone()[0] != 1:
                    raise Exception("Failed to verify database read access")
                
                logging.info("Successfully verified database read access")
                
                # Try to insert a test record with a more resilient approach
                try:
                    self._ensure_account_exists(cursor, "-999")
                    
                    # Verify the account exists with a simple read
                    cursor.execute("""
                        SELECT COUNT(*) FROM account_totals WHERE account_id = -999
                    """)
                    
                    count = cursor.fetchone()[0]
                    if count == 0:
                        logging.warning("Test account does not exist, but database is accessible")
                    else:
                        logging.info("Successfully verified test account exists")
                    
                    # Removed database health check
                    
                    # Apply VACUUM optimizations if needed
                    self._apply_vacuum_optimizations(connection)
                        
                    logging.info("Successfully verified database access")
                    return
                except Exception as write_error:
                    # If write fails but read succeeded, log warning but continue
                    logging.warning(f"Database write test failed, but read succeeded: {str(write_error)}")
                    logging.info("Continuing with read-only verification")
                    return
                
        except Exception as e:
            logging.error(f"Failed to verify database access: {type(e).__name__}: {str(e)}")
            raise RuntimeError(f"Database initialization failed - could not access database: {str(e)}")
    
    def _apply_vacuum_optimizations(self, connection):
        """Apply VACUUM optimizations to tables if needed"""
        try:
            # Only apply optimizations occasionally to avoid overhead
            current_time = time.time()
            last_optimize_time = getattr(self, '_last_optimize_time', 0)
            
            # Apply optimizations once per day by default
            optimize_interval = int(os.getenv("DB_OPTIMIZE_INTERVAL", "86400"))
            
            if current_time - last_optimize_time > optimize_interval:
                logging.info("Applying VACUUM optimizations")
                
                if hasattr(connection, '_optimize_table_storage'):
                    cursor = connection.get_cursor()
                    connection._optimize_table_storage(cursor)
                    connection.commit()
                
                self._last_optimize_time = current_time
                logging.info("VACUUM optimizations applied")
        except Exception as e:
            # Don't fail initialization if optimization fails
            logging.warning(f"Failed to apply VACUUM optimizations: {str(e)}")

    @contextmanager
    def get_transaction(self):
        """Get a transaction context manager for safe database operations with improved error handling"""
        db = self.get_validated_connection()
        transaction_id = f"txn-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
        logging.debug(f"Starting transaction {transaction_id}")
        
        try:
            db.begin_transaction()
            yield db
            # If we got here without exception, attempt to commit
            try:
                db.commit()
                logging.debug(f"Transaction {transaction_id} committed successfully")
            except Exception as commit_error:
                error_type = type(commit_error).__name__
                error_msg = str(commit_error)
                logging.error(f"Commit failed for transaction {transaction_id}: {error_type}: {error_msg}")
                
                # Check for specific errors that indicate an aborted transaction
                is_transaction_aborted = "current transaction is aborted" in error_msg.lower() or "25P02" in error_msg
                
                # Try to roll back even after commit failure
                try:
                    db.rollback()
                    logging.debug(f"Transaction {transaction_id} rolled back after commit failure")
                except Exception:
                    pass
                
                # Invalidate the connection if it's in an aborted state
                if is_transaction_aborted and hasattr(self._local, 'db'):
                    try:
                        self._local.db.invalidate()
                        delattr(self._local, 'db')
                        logging.warning(f"Connection invalidated after aborted transaction {transaction_id}")
                    except Exception:
                        pass
                
                # Re-raise the original commit error
                raise
                
        except Exception as e:
            logging.error(f"Transaction {transaction_id} failed: {type(e).__name__}: {str(e)}")
            
            # Examine the error to see if it's related to an aborted transaction
            error_type = type(e).__name__
            error_details = str(e)
            is_transaction_aborted = "current transaction is aborted" in error_details.lower() or "25P02" in error_details
            
            try:
                # Even if transaction is already aborted, try to roll back to reset the transaction state
                db.rollback()
                logging.debug(f"Transaction {transaction_id} rolled back")
            except Exception as rollback_error:
                logging.error(f"Error during rollback of transaction {transaction_id}: {type(rollback_error).__name__}: {str(rollback_error)}")
                # Force connection invalidation for rollback failures
                if hasattr(self._local, 'db'):
                    try:
                        self._local.db.invalidate()
                        delattr(self._local, 'db')
                    except Exception:
                        pass
            
            # For transaction aborted errors, always invalidate the connection
            if is_transaction_aborted and hasattr(self._local, 'db'):
                try:
                    self._local.db.invalidate()
                    delattr(self._local, 'db')
                    logging.warning(f"Connection invalidated after aborted transaction {transaction_id}")
                except Exception:
                    pass
            
            # Specifically target network errors for detailed logging
            is_network_error = isinstance(error_type, (exc.InterfaceError, exc.OperationalError, exc.TimeoutError, exc.DisconnectionError)) or "network error" in error_details.lower()
            
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

            # Log environment information for debugging in production
            if self.env == "prod":
                env = os.getenv("ENVIRONMENT", "Not set")
                instance_name = os.getenv("INSTANCE_CONNECTION_NAME", "Not set")
                logging.error(f"Environment context for transaction {transaction_id}: ENVIRONMENT={env}, INSTANCE_CONNECTION_NAME={instance_name}")
            
            # Re-raise the original exception
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
        try:
            connection._ensure_tables_exist(connection)
        except Exception as e:
            logging.warning(f"Error checking/updating last_updated column: {str(e)}")
        
        if self.env == "dev":
            connection.commit()
    
    def _ensure_account_exists(self, cursor, account_id):
        cursor.execute("""
            INSERT INTO account_totals 
                (account_id, token_balance_in, token_balance_out, word_balance,
                    total_token_usage_in, total_token_usage_out, total_word_usage, transactions)
            VALUES 
                (%s, 3000000, 1000000, 10000, 0, 0, 0, 0)
            ON CONFLICT (account_id) DO NOTHING
        """, (account_id,))
        return True

    def _check_error_message(error_type, error_msg, backoff_base, retry_count):
        # Check for specific errors that might be retryable
        is_lock_timeout = "lock timeout" in error_msg.lower() or "55P03" in error_msg
        is_statement_timeout = "statement timeout" in error_msg.lower() or "57014" in error_msg
        is_deadlock = "deadlock detected" in error_msg.lower() or "40P01" in error_msg
        is_serialize_failure = "could not serialize access" in error_msg.lower() or "40001" in error_msg
        is_transaction_aborted = "current transaction is aborted" in error_msg.lower() or "25P02" in error_msg
        is_failed_transaction = "in failed transaction" in error_msg.lower()
        is_lock_contention = "could not acquire lock" in error_msg.lower()
        # Add check for network/connection errors based on type
        is_network_error = isinstance(error_type, (exc.InterfaceError, exc.OperationalError, exc.TimeoutError, exc.DisconnectionError)) or "network error" in error_msg.lower()
        
        # For recoverable errors, retry with backoff
        is_retryable = (is_lock_timeout or is_statement_timeout or is_deadlock or 
                        is_serialize_failure or is_transaction_aborted or 
                        is_failed_transaction or is_lock_contention or is_network_error)
        wait_time = None
        error_category = "unknown"

        if is_retryable :
            # Shorter exponential backoff with small random jitter
            backoff = backoff_base * (1.5 ** (retry_count - 1))  # Less aggressive exponential growth
            jitter = random.uniform(0, backoff * 0.2)  # 20% jitter
            wait_time = (backoff + jitter) # Calculate wait time in milliseconds
            # Ensure wait_time is never None if is_retryable is True
            if wait_time is None:
                wait_time = backoff_base # Default to base backoff if calculation failed somehow

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
        return {
            "is_retryable" : is_retryable,
            "error_category" : error_category,
            "wait_time" : wait_time,
            "wait_time_seconds" : wait_time / 1000.0
        }

    def _update_balance(self, cursor, account_id: int, prompt_tokens: int, completion_tokens: int, word_count: int, transaction_id: str) -> None:
        # First ensure the account exists in a separate transaction to avoid lock contention
        try:
            self._ensure_account_exists(cursor, account_id)
        except Exception as e:
            logging.warning(f"[ID: {transaction_id}] Account creation attempt failed: {str(e)}")
        
        # Now update the account balance
        today = datetime.now().strftime("%Y-%m-%d")

        # Use the FOR UPDATE SKIP LOCKED approach to avoid waiting on locks
        # This will either update immediately or skip if locked
        cursor.execute("""
            WITH locked_account AS (
                SELECT account_id 
                FROM account_totals 
                WHERE account_id = %s
                FOR UPDATE SKIP LOCKED
            )
            UPDATE account_totals
            SET token_balance_in = GREATEST(0, token_balance_in - %s),
                token_balance_out = GREATEST(0, token_balance_out - %s), 
                word_balance = GREATEST(0, word_balance - %s),
                transactions = transactions + 1,
                total_token_usage_in = total_token_usage_in + %s,
                total_token_usage_out = total_token_usage_out + %s,
                total_word_usage = total_word_usage + %s,
                last_updated = CURRENT_TIMESTAMP
            WHERE account_id = %s
                AND account_id IN (SELECT account_id FROM locked_account)
                AND token_balance_in >= %s
                AND token_balance_out >= %s
                AND word_balance >= %s
            RETURNING token_balance_in, token_balance_out, word_balance, 
                    transactions, total_token_usage_in, total_token_usage_out, 
                    total_word_usage
        """, (
            account_id,
            prompt_tokens, completion_tokens, word_count,
            prompt_tokens, completion_tokens, word_count,
            account_id, 
            prompt_tokens, completion_tokens, word_count
        ))
        
        updated_result = cursor.fetchone()
        if not updated_result:
            # Check if it's a lock issue or insufficient balance
            cursor.execute("""
                SELECT token_balance_in, token_balance_out, word_balance,
                        pg_try_advisory_lock(account_id) as has_lock
                FROM account_totals 
                WHERE account_id = %s
            """, (account_id,))
            
            balance = cursor.fetchone()

            if balance:
                if not balance[3]:  # Could not get advisory lock
                    # This is likely a lock contention issue, retry
                    raise RuntimeError("Could not acquire lock on account, will retry")
                
                # Check if it's an insufficient balance issue
                if (balance[0] < prompt_tokens or 
                    balance[1] < completion_tokens or 
                    balance[2] < word_count):
                    logging.error(f"Insufficient balance for account {account_id}: has ({balance[0]}, {balance[1]}, {balance[2]}), needs ({prompt_tokens}, {completion_tokens}, {word_count})")
                    raise RuntimeError(f"Insufficient token balance for account {account_id}")
                else:
                    # Some other issue, retry
                    raise RuntimeError("Update failed but account exists and has sufficient balance, will retry")
            else:
                # This should never happen with our upsert, but just in case
                raise RuntimeError(f"Failed to create or update account {account_id}")
    
        # Now update the daily summary in a new transaction to avoid failures affecting the balance update
        try:
            cursor.execute("""
                INSERT INTO account_daily_summary 
                (account_id, date, transaction_count, daily_token_usage_in, 
                    daily_token_usage_out, daily_word_usage)
                VALUES (%s, %s, 1, %s, %s, %s)
                ON CONFLICT (account_id, date) DO UPDATE SET
                    transaction_count = account_daily_summary.transaction_count + 1,
                    daily_token_usage_in = account_daily_summary.daily_token_usage_in + EXCLUDED.daily_token_usage_in,
                    daily_token_usage_out = account_daily_summary.daily_token_usage_out + EXCLUDED.daily_token_usage_out,
                    daily_word_usage = account_daily_summary.daily_word_usage + EXCLUDED.daily_word_usage
            """, (account_id, today, prompt_tokens, completion_tokens, word_count))
        except Exception as daily_error:
            # If daily summary update fails but balance update succeeded, log warning but continue
            logging.warning(f"[ID: {transaction_id}] Daily summary update failed but balance updated: {str(daily_error)}")
        
        # Success! Get the updated balance
        final_balance = {
            'token_balance_in': float(updated_result[0]),
            'token_balance_out': float(updated_result[1]),
            'word_balance': int(updated_result[2]),
            'transactions': int(updated_result[3]),
            'total_token_usage_in': float(updated_result[4]),
            'total_token_usage_out': float(updated_result[5]),
            'total_word_usage': int(updated_result[6])
        }
        return final_balance

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
        
        # Get connection-specific parameters for this critical operation
        connection = None 
        max_retries = DEFAULT_TOKEN_UPDATE_RETRIES
        retry_count = 0
        backoff_base = DEFAULT_TOKEN_UPDATE_BACKOFF_BASE
        
        # Ensure we get fresh connections for each attempt to avoid reusing a connection in a failed state
        while retry_count < max_retries:
            try:
                # Get a fresh connection for each retry attempt to avoid failed transaction blocks
                connection = self._get_connection(force_new=(retry_count > 0))
                max_retries = connection.token_update_retries
                
                # Use a simpler, more direct transaction
                with self.get_transaction() as db:
                    final_balance = self._update_balance(db.get_cursor(), account_id, prompt_tokens, completion_tokens, word_count, transaction_id)
                    db.commit()

                    balance_msg = f"\n====== DATABASE UPDATE SUCCEEDED [ID: {transaction_id}] ======\n"
                    balance_msg += f"Account: {account_id}\n"
                    balance_msg += f"Final Balance: {final_balance}\n"
                    balance_msg += f"================================================================"
                    logging.info(f"\033[32m{balance_msg}\033[0m")  # Using \033[32m for bright green
                    

                    
                    return final_balance
                    
            except Exception as e:
                retry_count += 1
                error_type = type(e).__name__
                error_msg = str(e)
                
                error_data = self._check_error_message(error_type, error_msg, backoff_base, retry_count)
                is_retryable = error_data["is_retryable"]
                wait_time_seconds = error_data["wait_time_seconds"]
                error_category = error_data["error_category"]
                
                if is_retryable and retry_count < max_retries:
                    logging.warning(
                        f"[ID: {transaction_id}] Database {error_category} detected "
                        f"(attempt {retry_count}/{max_retries}): {error_type}: {error_msg}. "
                        f"Retrying in {wait_time_seconds:.2f}s"
                    )
                    
                    # Sleep with backoff
                    time.sleep(wait_time_seconds)
                    
                    # For any error, force a new connection on the next attempt
                    # This is handled at the beginning of the loop with force_new=True
                elif "insufficient token balance" in error_msg.lower():
                    # Don't retry for insufficient balance - this is an expected condition
                    logging.error(f"[ID: {transaction_id}] Account has insufficient balance: {error_msg}")
                    raise RuntimeError(f"Insufficient token balance: {error_msg}")
                else:
                    # For other errors or if we've exhausted retries, raise the error
                    logging.error(f"[ID: {transaction_id}] CRITICAL: Failed to update token usage in database: {error_type}: {error_msg}")
                    raise RuntimeError(f"Failed to update token usage: {error_msg}") from e
        
        # If we've exhausted all retries
        if retry_count >= max_retries:
            error_msg = f"Failed to update token usage after {max_retries} attempts"
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
                    transactions INTEGER NOT NULL DEFAULT 0 CHECK (transactions >= 0),
                    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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
                
                # Create or update indices for better performance
                cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_account_totals_usage 
                ON account_totals(token_balance_in, token_balance_out, word_balance)
                """)
                
                cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_summary_date 
                ON account_daily_summary(date)
                """)

        except Exception as e:
            logging.error(f"Error creating tables: {str(e)}")
            raise

    def check_sufficient_balance(self, account_id: int, prompt_tokens: int, completion_tokens: int, word_count: int = 0) -> tuple[bool, dict]:
        """Check if account has sufficient balance for the requested operation without updating the balance."""
        transaction_id = f"chk-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
        max_retries = DEFAULT_MAX_RETRIES
        retry_count = 0
        backoff_base = DEFAULT_TOKEN_UPDATE_BACKOFF_BASE
        
        while retry_count < max_retries:
            try:
                # Get a fresh connection for each retry attempt
                connection = self._get_connection(force_new=(retry_count > 0))
                
                # Use a completely non-locking approach for balance checks
                # This is safe because we're only reading, and we'll do a proper check with locks during the actual update
                cursor = connection.get_cursor()
                
                # Skip setting timeouts as they're already configured as needed
                
                try:
                    # First ensure the account exists with a non-blocking insert
                    # Use a separate connection for the insert to avoid transaction conflicts
                    with self.get_transaction() as db:
                        self._ensure_account_exists(db.get_cursor(), account_id)
                except Exception as e:
                    # Log but continue - if account exists this will fail harmlessly
                    logging.debug(f"[ID: {transaction_id}] Account creation attempt (non-critical): {str(e)}")
                
                # Simple direct query with NO LOCK - absolute fastest approach
                # We explicitly avoid any locking here since this is just a check
                cursor.execute("""
                    SELECT 
                        token_balance_in, 
                        token_balance_out, 
                        word_balance, 
                        transactions
                    FROM account_totals
                    WHERE account_id = %s
                """, (account_id,))
                
                result = cursor.fetchone()
                if not result:
                    # If account doesn't exist after our insert attempt, something is wrong
                    # But let's return a default balance instead of failing
                    logging.warning(f"[ID: {transaction_id}] Account {account_id} not found, using default values")
                    return True, {
                        "token_balance_in": self.DEFAULT_TOKEN_BALANCE_IN,
                        "token_balance_out": self.DEFAULT_TOKEN_BALANCE_OUT,
                        "word_balance": self.DEFAULT_WORD_BALANCE,
                        "transactions": 0
                    }
                
                # Calculate sufficient balance directly
                # For balance checks, it's better to assume sufficient balance than to fail the request
                token_balance_in = float(result[0]) if result[0] is not None else self.DEFAULT_TOKEN_BALANCE_IN
                token_balance_out = float(result[1]) if result[1] is not None else self.DEFAULT_TOKEN_BALANCE_OUT
                word_balance = int(result[2]) if result[2] is not None else self.DEFAULT_WORD_BALANCE
                transactions = int(result[3]) if result[3] is not None else 0
                
                has_sufficient_balance = (
                    token_balance_in >= prompt_tokens and 
                    token_balance_out >= completion_tokens and 
                    word_balance >= word_count
                )
                
                current_balance = {
                    "token_balance_in": token_balance_in,
                    "token_balance_out": token_balance_out,
                    "word_balance": word_balance,
                    "transactions": transactions
                }
                
                return has_sufficient_balance, current_balance
                    
            except Exception as e:
                retry_count += 1
                error_type = type(e).__name__
                error_msg = str(e)
                
                error_data = self._check_error_message(error_type, error_msg, backoff_base, retry_count)
                is_retryable = error_data["is_retryable"]
                wait_time = error_data["wait_time"]
                error_category = error_data["error_category"]

                if is_retryable and retry_count < max_retries:
                    logging.warning(f"[ID: {transaction_id}] Balance check {error_category} error (attempt {retry_count}/{max_retries}): {error_type}: {error_msg}")
                    time.sleep(wait_time)
                else:
                    # If we've exhausted retries or have a non-retryable error, log it
                    logging.error(f"Error checking balance: {error_type}: {error_msg}")
                    
                    # For balance checks, it's better to assume sufficient balance than to fail the request
                    # The actual update will still verify the balance before deducting tokens
                    logging.warning(f"[ID: {transaction_id}] Assuming sufficient balance after error")
                    return True, {
                        "token_balance_in": self.DEFAULT_TOKEN_BALANCE_IN,
                        "token_balance_out": self.DEFAULT_TOKEN_BALANCE_OUT,
                        "word_balance": self.DEFAULT_WORD_BALANCE,
                        "transactions": 0
                    }
        
        # If we've exhausted all retries, assume sufficient balance
        # This is safer than failing the request, as the actual update will still check the balance
        logging.warning(f"[ID: {transaction_id}] Assuming sufficient balance after {max_retries} failed attempts")
        return True, {
            "token_balance_in": self.DEFAULT_TOKEN_BALANCE_IN,
            "token_balance_out": self.DEFAULT_TOKEN_BALANCE_OUT,
            "word_balance": self.DEFAULT_WORD_BALANCE,
            "transactions": 0
        }

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