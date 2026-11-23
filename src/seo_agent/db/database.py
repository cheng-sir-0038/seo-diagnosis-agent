from sqlalchemy.engine import URL

from src.core.database import PostgresDatabase
from src.seo_agent.db.models import Base


class SeoAgentDatabase(PostgresDatabase):
    def __init__(self, url: str | URL | None = None, *, schema: str | None = None) -> None:
        super().__init__(Base.metadata, url, schema=schema)
