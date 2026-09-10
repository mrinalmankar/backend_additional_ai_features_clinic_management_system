import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.deps import DbSession
from app.api.routes import api_router
from app.core.config import settings
from app.core.database import Base, engine
from app.core.limiter import limiter
from app.services.gbp_scheduler import start_scheduler, stop_scheduler

from app.models import consultation as _consultation
from app.models import gbp as _gbp
from app.models import prescription as _prescription
from app.models import reference as _reference

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

@asynccontextmanager
async def lifespan(app: FastAPI):

    settings.validate_for_production()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                ALTER TABLE gbp_accounts
                ADD COLUMN IF NOT EXISTS access_token_enc TEXT;
                """
            )
        )
    start_scheduler()
    yield
    stop_scheduler()

app = FastAPI(
    title="Clinic AI Assistant - Module 1",
    description="AI Clinical Assistant: consultation summaries, prescriptions, OCR, transcription",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/debug/config")
def debug_config():
    return {
        "environment": os.getenv("ENVIRONMENT"),
        "llm_provider": os.getenv("LLM_PROVIDER"),
        "phi_llm_allowed": os.getenv("ALLOW_THIRD_PARTY_LLM_FOR_PHI"),
        "jwt_secret_loaded": bool(os.getenv("JWT_SECRET_KEY")),
        "groq_api_key_loaded": bool(os.getenv("GROQ_API_KEY")),
        "database_url_loaded": bool(os.getenv("DATABASE_URL")),
    }

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(api_router)

@app.get("/health")
async def health(db: DbSession) -> dict[str, str]:
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    return {"status": "ok"}
