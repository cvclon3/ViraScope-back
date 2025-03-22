# app/core/database.py
from sqlmodel import create_engine, SQLModel, Session
from app.core.config import settings
from typing import Generator, Annotated
from fastapi import Depends

engine = create_engine(settings.database_url, echo=True)


def get_db() -> Generator:
    with Session(engine) as session:
        yield session


def init_db():
    SQLModel.metadata.create_all(engine)


SessionDep = Annotated[Session, Depends(get_db)]
