"""Optional PostgreSQL foundation; no candidate data is persisted in phase one."""
import os
from sqlalchemy import create_engine, text


def check_database() -> bool:
    url = os.getenv("DATABASE_URL", "postgresql+psycopg://resumepilot:resumepilot@localhost:5432/resumepilot")
    with create_engine(url).connect() as connection:
        return connection.execute(text("SELECT 1")).scalar() == 1
