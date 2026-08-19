import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATA_DIR, DATABASE_PATH

DATA_DIR.mkdir(parents=True, exist_ok=True)

def _database_url() -> str:
    """Return a SQLAlchemy URL, accepting CloudBase's common ``mysql://`` form."""
    url = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH.resolve()}")
    if url.startswith("mysql://"):
        return "mysql+pymysql://" + url.removeprefix("mysql://")
    return url


DATABASE_URL = _database_url()
_engine_options: dict = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite:"):
    _engine_options["connect_args"] = {"check_same_thread": False}
else:
    _engine_options["pool_recycle"] = 1800
    _engine_options["connect_args"] = {
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "read_timeout": 120,
        "write_timeout": 120,
    }

engine = create_engine(DATABASE_URL, **_engine_options)


if DATABASE_URL.startswith("sqlite:"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_db_and_tables():
    Base.metadata.create_all(bind=engine)
