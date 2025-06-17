from sqlalchemy.engine.base import Engine
from sqlalchemy.engine.interfaces import DBAPICursor

class DatabaseConnectionPool:
    engine: Engine | None
    isolation_level: str
    last_validation_time: float

    def open_session(
        self, transactional=True, transaction_id: str | None = None
    ) -> DBAPICursor: ...
    def close(self) -> None: ...

class PostgreSQLConnectionPool(DatabaseConnectionPool):
    def __init__(
        self, database_url, instance_connection_name=None, private_ip=False
    ) -> None: ...
