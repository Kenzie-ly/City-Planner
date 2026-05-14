import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Fallback for local development
    DATABASE_URL = "postgresql+psycopg2://postgres:password@localhost:5432/transport_ai2"
    logger.warning("DATABASE_URL not set — falling back to local development database.")

# Cloud Run / serverless: use NullPool to avoid stale connections across
# container lifecycle events. Each request opens and closes its own connection.
# If running locally (TCP), a standard pool is fine.
_is_cloud_sql_socket = "/cloudsql/" in DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    # NullPool is mandatory for Cloud Run to prevent connection leaks
    poolclass=NullPool,
    # Echo SQL only when debugging — disable in production
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

