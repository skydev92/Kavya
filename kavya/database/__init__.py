import logging
import os

from .database import Database

# Check for required PostgreSQL dependencies
required_deps = ["google.cloud.sql.connector", "pg8000", "sqlalchemy"]
if os.getenv("INSTANCE_CONNECTION_NAME"):  # If using Cloud SQL
    for dep in required_deps:
        try:
            __import__(dep.split(".")[0])
        except ImportError:
            error_msg = f"CRITICAL: Production environment requires PostgreSQL dependencies but {dep} is missing"
            logging.critical(error_msg)
            raise RuntimeError(error_msg)

# Configure basic logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
