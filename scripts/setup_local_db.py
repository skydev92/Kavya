import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def run_command(command, shell=False):
    try:
        result = subprocess.run(
            command, shell=shell, check=True, text=True, capture_output=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {e.cmd}")
        logger.error(f"Error output: {e.stderr}")
        if "already exists" not in str(e.stderr):  # Ignore "already exists" errors
            raise


def setup_local_database():
    """Set up the local PostgreSQL database for development."""

    # Default values
    DB_USER = "kavya_user"
    DB_NAME = "kavya_db"
    DB_PASS = "postgres"  # Default password for local development

    logger.info("Setting up local PostgreSQL database...")

    try:
        # Create user if it doesn't exist
        logger.info(f"Creating database user {DB_USER}...")
        run_command(
            [
                "createuser",
                "-s",  # Superuser
                "-d",  # Can create databases
                "-l",  # Can login
                DB_USER,
            ]
        )

        # Create database if it doesn't exist
        logger.info(f"Creating database {DB_NAME}...")
        run_command(
            [
                "createdb",
                "-O",
                DB_USER,  # Set owner
                "-E",
                "UTF8",  # Set encoding
                DB_NAME,
            ]
        )

        # Set user password
        logger.info("Setting user password...")
        run_command(
            [
                "psql",
                "-d",
                DB_NAME,
                "-c",
                f"ALTER USER {DB_USER} WITH PASSWORD '{DB_PASS}';",
            ]
        )

        # Create .env file if it doesn't exist
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if not os.path.exists(env_path):
            logger.info("Creating .env file...")
            with open(env_path, "w") as f:
                f.write(
                    f"""ENVIRONMENT=dev
OPENAI_API_KEY=your-openai-api-key
ANTHROPIC_API_KEY=your-anthropic-api-key
GROQ_API_KEY=your-groq-api-key
GEMINI_API_KEY=your-gemini-api-key
MISTRAL_API_KEY=your-mistral-api-key
DATABASE_URL=postgresql://{DB_USER}:{DB_PASS}@localhost:5432/{DB_NAME}
"""
                )

        logger.info(
            """
✅ Local database setup complete!

Your database connection details:
- Host: localhost
- Port: 5432
- Database: kavya_db
- User: kavya_user
- Password: postgres

These are stored in your .env file as DATABASE_URL.

To test the connection, you can run:
    psql -h localhost -U kavya_user -d kavya_db

When prompted for password, enter: postgres
"""
        )

    except Exception as e:
        logger.error(f"❌ Error setting up database: {str(e)}")
        logger.error(
            """
Troubleshooting tips:
1. Make sure PostgreSQL is running:
   brew services start postgresql@17
   
2. If you get permission errors, try:
   sudo chown -R $(whoami) /opt/homebrew/var/postgresql@17

3. If you still have issues, you can reset PostgreSQL:
   brew services stop postgresql@17
   rm -rf /opt/homebrew/var/postgresql@17
   brew services start postgresql@17
"""
        )
        sys.exit(1)


if __name__ == "__main__":
    setup_local_database()
