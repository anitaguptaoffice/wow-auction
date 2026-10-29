"""Local preview API. No production import, reset, login, or cloud mutations."""

from contextlib import asynccontextmanager
import os
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from app.icon_api import router as icon_router

from compact_market.store import MarketStore, SORTS
from compact_market.unified import UnifiedMarketStore


@asynccontextmanager
async def lifespan(app):
    store_class = UnifiedMarketStore if os.environ.get("COMPACT_ENGINE") == "unified" else MarketStore
    app.state.store = store_class(Path(os.environ.get("COMPACT_DATABASE", "data/compact/market.duckdb")),
                                  threads=int(os.environ.get("COMPACT_THREADS", "2")),
                                  memory_limit=os.environ.get("COMPACT_MEMORY_LIMIT", "1GB"))
    yield
    app.state.store.close()


app = FastAPI(title="WoW Auction", lifespan=lifespan)
app.include_router(icon_router)


@app.middleware("http")
async def timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - start) * 1000:.3f}"
    return response


def call(request, method, **params):
    try:
        return getattr(request.app.state.store, method)(**params)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).with_name("preview.html"))


@app.get("/health")
def health(request: Request):
    store = request.app.state.store
    return {"status": "ok", "engine": "compact-duckdb", "scans": len(store.scans),
            "summaries": len(store.records)}


@app.get("/api/market/catalog")
def catalog(request: Request):
    return call(request, "catalog")


@app.get("/api/market/status")
def status(request: Request, scan_id: int | None = Query(None, ge=1)):
    return call(request, "status", scan_id=scan_id)


@app.get("/api/market/items")
def items(request: Request, scan_id: int | None = Query(None, ge=1),
          q: str = Query("", max_length=100), collection: str = "", sort: str = "price_asc",
          page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    params = dict(scan_id=scan_id, q=q, sort=sort, page=page, page_size=page_size)
    if isinstance(request.app.state.store, UnifiedMarketStore):
        params['collection'] = collection
    elif collection:
        raise HTTPException(422, 'Collections require unified engine')
    return call(request, "items", **params)


@app.get("/api/market/listings")
def listings(request: Request, market_key: str = Query(..., max_length=512),
             scan_id: int | None = Query(None, ge=1),
             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    return call(request, "listings", market_key=market_key, scan_id=scan_id,
                page=page, page_size=page_size)


@app.get("/api/market/history")
def history(request: Request, market_key: str = Query(..., max_length=512),
            scan_id: int | None = Query(None, ge=1)):
    return call(request, "history", market_key=market_key, scan_id=scan_id)


@app.get("/api/market/history/view")
def public_history(request: Request, market_key: str = Query(..., max_length=512),
                   scan_id: int = Query(..., ge=1)):
    return call(request, "public_history", market_key=market_key, scan_id=scan_id)
