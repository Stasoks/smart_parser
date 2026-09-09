from __future__ import annotations

from datetime import datetime
from typing import Iterator

from sqlmodel import Field, Session, SQLModel, create_engine

from .config import get_settings


class SearchRun(SQLModel, table=True):
    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    prompt: str = ""
    search_spec_json: str = ""
    status: str = "queued"
    result_count: int = 0
    error: str = ""


class SavedListing(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    search_id: str = Field(index=True)
    external_id: str = Field(index=True)
    title: str
    price: int | None = None
    url: str
    deal_score: float = 0.0
    expected_profit: float | None = None
    feature_map_json: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
