"""Public read-only auction API plus the token-protected import control plane."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin_api import router as admin_router
from app.database import create_db_and_tables
from app.icon_api import router as icon_router
from app.market_api import router as market_router


def _cors_origins() -> list[str]:
    configured = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
    local = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
    ]
    return list(dict.fromkeys(configured + local))


create_db_and_tables()

app = FastAPI(title="WoW Auction Market API")
app.include_router(market_router)
app.include_router(icon_router)
app.include_router(admin_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health", include_in_schema=False)
def health_check():
    return {"status": "ok", "service": "wow-auction-api"}
