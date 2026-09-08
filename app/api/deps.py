from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentDoctor
from app.core.database import get_db

DbSession = Annotated[AsyncSession, Depends(get_db)]

__all__ = ["DbSession", "CurrentDoctor"]
