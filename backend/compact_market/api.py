"""Local preview API. No production import, reset, login, or cloud mutations."""

from contextlib import asynccontextmanager
import os
import json
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from app.icon_api import router as icon_router
from app.icon_names import safe_icon_name

from compact_market.store import MarketStore, SORTS
from compact_market.unified import UnifiedMarketStore


@asynccontextmanager
async def lifespan(app):
    store_class = UnifiedMarketStore if os.environ.get("COMPACT_ENGINE") == "unified" else MarketStore
    app.state.store = store_class(Path(os.environ.get("COMPACT_DATABASE", "data/compact/market.duckdb")),
                                  threads=int(os.environ.get("COMPACT_THREADS", "2")),
                                  memory_limit=os.environ.get("COMPACT_MEMORY_LIMIT", "1GB"))
    manifest = app.state.store.database.parent / 'icon-map.json'
    app.state.store.icon_map = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {}
    if any(not safe_icon_name(v) for v in app.state.store.icon_map.values()):
        app.state.store.close()
        raise ValueError('Unsafe icon manifest')
    app.state.icon_names = frozenset(app.state.store.icon_map.values())
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
    result = call(request, "status", scan_id=scan_id)
    state_path = os.getenv('COMPACT_UPDATE_STATUS_FILE')
    if state_path:
        try:
            state = json.loads(Path(state_path).read_text(encoding='utf-8'))
            result.update(updateState=state['phase'], lastUpdateAttemptAt=state.get('updated_at'))
        except (OSError, ValueError, KeyError):
            result['updateState'] = 'unknown'
    return result


@app.get("/api/market/items")
def items(request: Request, scan_id: int | None = Query(None, ge=1),
          q: str = Query("", max_length=100), collection: str = "", sort: str = "price_asc",
          difficulty: str = Query('', max_length=1), stats: str = Query('', max_length=5),
          socket: str = Query('', max_length=3), tertiary: str = Query('', max_length=20),
          page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    params = dict(scan_id=scan_id, q=q, sort=sort, page=page, page_size=page_size)
    if isinstance(request.app.state.store, UnifiedMarketStore):
        params['collection'] = collection
        params.update(difficulty=difficulty, stats=stats, socket=socket, tertiary=tertiary)
    elif collection:
        raise HTTPException(422, 'Collections require unified engine')
    return call(request, "items", **params)


@app.get("/api/market/listings")
def listings(request: Request, market_key: str = Query(..., max_length=512),
             scan_id: int | None = Query(None, ge=1),
             difficulty: str = Query('', max_length=1), stats: str = Query('', max_length=5),
             socket: str = Query('', max_length=3), tertiary: str = Query('', max_length=20),
             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    return call(request, "listings", market_key=market_key, scan_id=scan_id,
                page=page, page_size=page_size, difficulty=difficulty, stats=stats, socket=socket, tertiary=tertiary,
                public_only=True)


@app.get("/api/market/history")
def history(request: Request, market_key: str = Query(..., max_length=512),
            scan_id: int | None = Query(None, ge=1)):
    return call(request, "history", market_key=market_key, scan_id=scan_id)


@app.get("/api/market/history/view")
def public_history(request: Request, market_key: str = Query(..., max_length=512),
                   scan_id: int = Query(..., ge=1), start: int | None = Query(None, ge=0),
                   end: int | None = Query(None, ge=0), max_points: int = Query(400, ge=8, le=2000),
                   difficulty: str = '', stats: str = '', socket: str = '', tertiary: str = ''):
    params = dict(market_key=market_key, scan_id=scan_id)
    if isinstance(request.app.state.store, UnifiedMarketStore):
        interval = int(os.getenv('COMPACT_EXPECTED_INTERVAL_SECONDS', '0'))
        params.update(start=start, end=end, max_points=max_points, gap_seconds=interval * 1.5,
                      difficulty=difficulty, stats=stats, socket=socket, tertiary=tertiary)
    return call(request, "public_history", **params)


@app.get('/api/market/compare')
def compare(request: Request, market_key: str = Query(..., max_length=512),
            scan_id: int | None = Query(None, ge=1),
            difficulty: str = '', stats: str = '', socket: str = '', tertiary: str = ''):
    if not isinstance(request.app.state.store, UnifiedMarketStore):
        raise HTTPException(422, 'Comparison requires unified engine')
    return call(request, 'compare', market_key=market_key, scan_id=scan_id,
                difficulty=difficulty, stats=stats, socket=socket, tertiary=tertiary)
