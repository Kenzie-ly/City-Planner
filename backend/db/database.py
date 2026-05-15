import os
import logging
import traceback
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "postgresql+psycopg2://postgres:password@localhost:5432/transport_ai2"
    logger.warning("DATABASE_URL not set -- falling back to local development database.")

# Engine creation
engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    echo=False,
)
# ---------------------------------------------------

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


