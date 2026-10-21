from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    with factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
