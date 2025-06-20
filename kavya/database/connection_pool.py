import logging
import os
import random
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import create_engine, event, exc
from sqlalchemy.engine.base import ExceptionContextImpl

from kavya.database.utils import generate_transaction_id

_ENV = os.getenv("ENVIRONMENT", "dev")


class DatabaseConnectionPool:
    """
    Base class for managing database connection pools.
    """

    def __init__(self) -> None:
        self.engine = None
        self.isolation_level = "READ COMMITTED"
        self.last_validation_time = 0

    def open_session(
        self, transactional=True, transaction_id: str | None = None
    ) -> Any:
        """
        Open a new session with the database.
        :param transactional: If True, the session will be transactional. If False, it will be non-transactional
        (autocommit).
        :param transaction_id: Optional transaction ID for logging purposes.
        :return: A connection object that can be used to interact with the database.
        """
        raise NotImplementedError

    def close(self) -> None:
        """
        Close the connection pool and release resources.
        """
        raise NotImplementedError


class PostgreSQLConnectionPool(DatabaseConnectionPool):
    """
    PostgreSQLConnectionPool manages a connection pool for PostgreSQL databases.
    """

    def __init__(
        self,
        database_url,
        instance_connection_name=None,
        private_ip=False,
        config: dict = None,
    ) -> None:
        super().__init__()
        if config is None:
            raise ValueError(
                "config is required for PostgreSQLConnectionPool – no in-code defaults allowed"
            )

        db_config = config["database"]

        self.pool_size = db_config["pool_size"]
        self.max_overflow = db_config["max_overflow"]
        self.pool_timeout = db_config["pool_timeout"]
        self.pool_recycle = db_config["pool_recycle"]
        self.max_retries = db_config["max_retries"]
        self.retry_backoff = db_config["retry_backoff"]
        self.connect_timeout = db_config["connect_timeout"]
        self.validation_interval = db_config["validation_interval"]

        self.database_url = database_url
        self.instance_connection_name = instance_connection_name
        self._connector = None
        self.private_ip = private_ip
        self.db_host = None
        self.db_port = None
        self.db_user = None
        self.db_pass = None
        self.db_name = None

        # Add VACUUM optimization settings
        self.fillfactor_account_totals = int(
            os.getenv("DB_FILLFACTOR_ACCOUNT_TOTALS", "90")
        )
        self.fillfactor_account_daily = int(
            os.getenv("DB_FILLFACTOR_ACCOUNT_DAILY", "95")
        )
        self.autovacuum_enabled = os.getenv(
            "DB_AUTOVACUUM_ENABLED", "true"
        ).lower() in ("true", "1", "yes")

        # Parse the database URL to extract connection details
        parsed = urlparse(database_url)
        self.db_user = parsed.username
        self.db_pass = parsed.password
        self.db_name = parsed.path.lstrip("/")

        if not instance_connection_name:
            # Parse host and port for local development
            self.db_host = parsed.hostname
            self.db_port = parsed.port

        postgresql_connect(self)

        try:
            postgresql_ensure_tables_exist(self)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logging.warning(
                f"⚠️ DB_TABLES_ERROR: Failed to ensure tables exist: "
                f"{error_type}: {error_msg}"
            )

    @contextmanager
    def open_session(
        self, transactional=True, transaction_id: str | None = None
    ) -> Any:
        start_time = time.time()
        if not transaction_id:
            transaction_id = generate_transaction_id()
        thread_id = threading.get_ident()
        connection = None
        backend_pid = -1

        try:
            connection = self.engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            )

            # Implicitly validates the connection and gets the backend PID for identification
            cursor = connection.connection.cursor()
            cursor.execute("SELECT pg_backend_pid()")
            result = cursor.fetchone()
            if result:
                backend_pid = result[0]
            cursor.close()

            # Reset isolation level based on transactional flag
            connection = connection.execution_options(
                isolation_level="READ COMMITTED" if transactional else "AUTOCOMMIT"
            )

            if transactional:
                connection.begin()

            duration = time.time() - start_time

            if duration > 10.0:
                logging.warning(
                    f"🐌 DB_CTX_VERY_SLOW: Thread-{thread_id} [ID: {transaction_id}] took {duration:.2f}s to establish connection!"
                )
            elif duration > 5.0:
                logging.warning(
                    f"🐌 DB_CTX_SLOW: Thread-{thread_id} [ID: {transaction_id}] took {duration:.2f}s to establish connection."
                )

            logging.info(
                f"✅ DB_CTX_START: Thread-{thread_id} [ID: {transaction_id}] established {'' if transactional else 'non-'}transactional "
                f"connection with backend PID {backend_pid} in {duration:.2f}s."
            )

            try:
                ret = connection.connection.cursor()

                try:
                    yield ret
                finally:
                    ret.close()

                if transactional:
                    connection.commit()
            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)
                logging.error(
                    f"⚠️ DB_CTX_ERROR: Thread-{thread_id} [ID: {transaction_id}] encountered an error during "
                    f"{'transactional' if transactional else 'non-transactional'} operation: "
                    f"{error_type}: {error_msg}"
                )

                if transactional:
                    try:
                        connection.rollback()
                    except Exception as rollback_error:
                        rollback_error_type = type(rollback_error).__name__
                        rollback_error_msg = str(rollback_error)

                        logging.error(
                            f"❌️ DB_CTX_ROLLBACK_ERROR: Thread-{thread_id} [ID: {transaction_id}] failed to rollback "
                            f"transaction: {rollback_error_type}: {rollback_error_msg}"
                        )

                raise e
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logging.error(
                f"💥 DB_CTX_CONNECT_ERROR: Thread-{thread_id} [ID: {transaction_id}] failed to establish "
                f"{'transactional' if transactional else 'non-transactional'} connection: "
                f"{error_type}: {error_msg}"
            )

            # Log additional diagnostic information
            _log_postgresql_connection_diagnostics()

            raise RuntimeError(f"Failed to establish database connection: {str(e)}")
        finally:
            if connection:
                try:
                    connection.close()  # Returns the connection to the pool
                except Exception as e:
                    error_type = type(e).__name__
                    error_msg = str(e)
                    logging.warning(
                        f"⚠️ DB_CTX_CLOSE_ERROR: Thread-{thread_id} [ID: {transaction_id}] failed to "
                        f"close connection: {error_type}: {error_msg}"
                    )

            logging.info(
                f"✅ DB_CTX_END: Thread-{thread_id} [ID: {transaction_id}] ended "
                f"{'transactional' if transactional else 'non-transactional'} context with backend PID {backend_pid}."
            )

    def close(self) -> None:
        """Close the connection and release resources"""
        logging.info("🚀 Closing database pool...")

        if self._connector:
            try:
                self._connector.close()
            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)
                logging.warning(
                    f"⚠️ DB_POOL_CLOSE_ERROR: Failed to close connector: "
                    f"{error_type}: {error_msg}"
                )
            self._connector = None

        if self.engine:
            try:
                self.engine.dispose()
            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)
                logging.warning(
                    f"⚠️ DB_POOL_DISPOSE_ERROR: Failed to dispose engine: "
                    f"{error_type}: {error_msg}"
                )
            self.engine = None

        logging.info("✅ Database pool closed successfully.")


def _log_postgresql_connection_diagnostics() -> None:
    """Log diagnostic information for connection issues"""
    try:
        # Log environment information
        env = os.getenv("ENVIRONMENT", "Not set")
        instance_name = os.getenv("INSTANCE_CONNECTION_NAME", "Not set")
        logging.error(
            f"Database connection environment: ENVIRONMENT={env}, INSTANCE_CONNECTION_NAME={instance_name}"
        )

        # Check if Cloud SQL Proxy is running
        if env == "prod" or instance_name != "Not set":
            try:
                result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
                proxy_running = "cloud-sql-proxy" in result.stdout
                logging.error(f"Cloud SQL Proxy running: {proxy_running}")

                # Check if port 5432 is open
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(("127.0.0.1", 5432))
                port_open = result == 0
                sock.close()
                logging.error(f"Port 5432 is {'open' if port_open else 'closed'}")
            except Exception as e:
                logging.error(f"Error checking Cloud SQL Proxy: {str(e)}")
    except Exception as e:
        logging.error(f"Error during connection diagnostics: {str(e)}")


# @formatter:off
def postgresql_ensure_tables_exist(db: "PostgreSQLConnectionPool") -> None:
    with db.open_session(transactional=True) as cursor:
        # Create account_totals table if it doesn't exist
        cursor.execute(
            """
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
            """
        )

        # Create account_daily_summary table if it doesn't exist
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS account_daily_summary (
                account_id INTEGER NOT NULL REFERENCES account_totals(account_id),
                date DATE NOT NULL,
                transaction_count INTEGER NOT NULL DEFAULT 0 CHECK (transaction_count >= 0),
                daily_token_usage_in NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (daily_token_usage_in >= 0),
                daily_token_usage_out NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (daily_token_usage_out >= 0),
                daily_word_usage INTEGER NOT NULL DEFAULT 0 CHECK (daily_word_usage >= 0),
                PRIMARY KEY (account_id, date)
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_account_totals_usage 
            ON account_totals(token_balance_in, token_balance_out, word_balance)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_daily_summary_date 
            ON account_daily_summary(date)
            """
        )

        # Update the account_totals table to include the last_updated column if it doesn't exist
        try:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns 
                WHERE table_name='account_totals' AND column_name='last_updated'
                """
            )
            has_last_updated = cursor.fetchone() is not None

            if not has_last_updated:
                cursor.execute(
                    """
                    ALTER TABLE account_totals ADD COLUMN last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    """
                )
                logging.info("Added last_updated column to account_totals table")
        except Exception as e:
            logging.warning(f"Error checking for last_updated column: {str(e)}")

        # Set FILLFACTOR for account_totals (frequently updated)
        cursor.execute(
            f"""
            ALTER TABLE account_totals SET (fillfactor = {db.fillfactor_account_totals})
            """
        )

        # Set FILLFACTOR for account_daily_summary (less frequently updated)
        cursor.execute(
            f"""
            ALTER TABLE account_daily_summary SET (fillfactor = {db.fillfactor_account_daily})
            """
        )

        # Configure autovacuum settings if enabled
        if db.autovacuum_enabled:
            # For account_totals (frequently updated, needs more aggressive vacuuming)
            cursor.execute(
                """
                ALTER TABLE account_totals SET (
                    autovacuum_vacuum_scale_factor = 0.05,
                    autovacuum_analyze_scale_factor = 0.02,
                    autovacuum_vacuum_threshold = 50,
                    autovacuum_analyze_threshold = 50
                )
                """
            )

            # For account_daily_summary (append-mostly table)
            cursor.execute(
                """
                ALTER TABLE account_daily_summary SET (
                    autovacuum_vacuum_scale_factor = 0.1,
                    autovacuum_analyze_scale_factor = 0.05,
                    autovacuum_vacuum_threshold = 100,
                    autovacuum_analyze_threshold = 100
                )
                """
            )


# @formatter:on
def postgresql_connect(db: "PostgreSQLConnectionPool") -> None:
    """
    Establish a connection to the PostgreSQL database.
    This method initializes the connection pool and sets up the necessary configurations.
    :raises RuntimeError: If the connection cannot be established
    """
    global _ENV
    start_time = time.time()
    logging.info(f"🚀 Creating new database pool in {_ENV} environment")

    if db.instance_connection_name:  # Production mode with Cloud SQL
        logging.info(
            f"🚀 Connecting to Cloud SQL instance: {db.instance_connection_name}"
        )

        db._connector = Connector(
            refresh_strategy="BACKGROUND"  # Changed from ALWAYS to BACKGROUND
        )

        def getconn() -> Any:
            try:
                logging.info(f"🚀 Establishing connection to Cloud SQL")

                conn = db._connector.connect(
                    db.instance_connection_name,
                    "pg8000",
                    user=db.db_user,
                    password=db.db_pass,
                    db=db.db_name,
                    ip_type=IPTypes.PRIVATE if db.private_ip else IPTypes.PUBLIC,
                    timeout=db.connect_timeout
                    / 1000,  # Convert from ms to seconds for pg8000
                )

                # Skip setting timeouts as they're already configured as needed
                logging.info(
                    f"✅ Successfully connected to Cloud SQL in {time.time() - start_time:.2f}s"
                )

                return conn
            except Exception as e:
                exception_type = type(e).__name__

                logging.error(
                    f"❌️ Failed to connect to Cloud SQL: {exception_type}: {str(e)}"
                )

                # Log additional diagnostic information
                _log_postgresql_connection_diagnostics()

                raise

        # Create engine with optimized settings
        db.engine = create_engine(
            "postgresql+pg8000://",
            creator=getconn,
            pool_size=db.pool_size,
            max_overflow=db.max_overflow,
            pool_timeout=db.pool_timeout / 1000,  # Convert from ms to seconds
            pool_recycle=db.pool_recycle / 1000,  # Convert from ms to seconds
            pool_pre_ping=True,
            isolation_level=db.isolation_level,
            echo=os.getenv("SQL_DEBUG", "").lower() in ("true", "1", "yes"),
        )
    else:  # Development mode with local PostgreSQL
        logging.info(f"🚀 Connecting to local PostgreSQL at {db.db_host}:{db.db_port}")

        # Define common connection options for local PostgreSQL
        connect_args = {
            "timeout": db.connect_timeout
            / 1000,  # Convert from ms to seconds for pg8000
        }

        db.engine = create_engine(
            db.database_url,
            pool_size=db.pool_size,
            max_overflow=db.max_overflow,
            pool_timeout=db.pool_timeout / 1000,  # Convert from ms to seconds
            pool_recycle=db.pool_recycle / 1000,  # Convert from ms to seconds
            pool_pre_ping=True,
            isolation_level=db.isolation_level,
            connect_args=connect_args,
            echo=os.getenv("SQL_DEBUG", "").lower() in ("true", "1", "yes"),
        )

        # For local connections, set timeouts on the first connection
        connection = db.engine.connect()

        try:
            # Skip setting timeouts as they're already configured as needed
            pass
        finally:
            connection.close()

        logging.info(
            f"✅ Successfully connected to local PostgreSQL in {time.time() - start_time:.2f}s"
        )

    # Configure retry handling using event listeners
    @event.listens_for(db.engine, "handle_error")
    def handle_error(context: ExceptionContextImpl) -> bool:
        error = context.original_exception
        conn = context.connection

        is_disconnect = (
            isinstance(
                error,
                (exc.DisconnectionError, exc.OperationalError, exc.TimeoutError),
            )
            or "network" in str(error).lower()
        )

        if is_disconnect:
            logging.warning(
                f"⚠️ Database connection error detected: {type(error).__name__}: {str(error)}"
            )
            # Invalidate and retry for connection errors
            conn.invalidate()
            return True

        is_deadlock = any(
            msg in str(error).lower()
            for msg in [
                "deadlock detected",
                "could not serialize access",
                "concurrent update",
                "lock timeout",
            ]
        )

        if is_deadlock:
            # Add exponential backoff for deadlocks
            retries = getattr(context, "_retry_count", 0)
            if retries < db.max_retries:
                setattr(context, "_retry_count", retries + 1)
                # Exponential backoff with jitter
                delay = db.retry_backoff * (2**retries) + random.uniform(0, 0.1)
                time.sleep(delay)
                logging.warning(
                    f"⚠️ Deadlock detected, retrying (attempt {retries + 1}/{db.max_retries})"
                )
                return True
            logging.error(f"Max retries ({db.max_retries}) exceeded for deadlock")
        return False

    logging.info(f"✅ Database pool created in {time.time() - start_time:.2f}s")
