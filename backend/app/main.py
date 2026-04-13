from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app import auth, models
from app.database import create_db_and_tables, get_db
from app.services.auction_cache import get_auction_history, get_cached_auction_items, start_monitoring
from app.services.auction_labels import enrich_auction_item_dict

limiter = Limiter(key_func=get_remote_address)

create_db_and_tables()

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    start_monitoring()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def register_user(request: Request, user: models.UserCreate, db: Session = Depends(get_db)):
    db_user = auth.get_user(db, username=user.username)
    if db_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")

    hashed_password = auth.get_password_hash(user.password)
    db_user = models.User(username=user.username, hashed_password=hashed_password, usage_count=10)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return {"message": f"User {user.username} registered successfully"}


@app.post("/login", response_model=models.Token)
@limiter.limit("5/minute")
def login_for_access_token(
    request: Request, form_data: Annotated[OAuth2PasswordRequestForm, Depends()], db: Session = Depends(get_db)
):
    user = auth.get_user(db, username=form_data.username)
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = auth.create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/query")
@limiter.limit("20/minute")
def query_data(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_active_user)],
    itemID: int,
    db: Session = Depends(get_db),
):
    if current_user.usage_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Usage limit exceeded. No more access attempts allowed."
        )

    current_user.usage_count -= 1
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    all_items = get_cached_auction_items()
    filtered_items = [
        enrich_auction_item_dict(dict(item)) for item in all_items if item.get("itemID") == itemID
    ]

    return {"data": filtered_items, "count": len(filtered_items), "remaining_uses": current_user.usage_count}


@app.get("/query/history")
@limiter.limit("20/minute")
def query_history(
    request: Request,
    current_user: Annotated[models.User, Depends(auth.get_current_active_user)],
    itemID: int,
    days: int = 7,
    itemLink: str | None = None,
    db: Session = Depends(get_db),
):
    """按物品 ID 返回保留期内的历史拍卖点（时间戳 + 一口价等），可选按完整 itemLink 过滤词缀。"""
    if current_user.usage_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Usage limit exceeded. No more access attempts allowed."
        )

    current_user.usage_count -= 1
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    d = max(1, min(days, 90))
    series = get_auction_history(itemID, days=d, item_link=itemLink)
    return {
        "itemID": itemID,
        "days": d,
        "count": len(series),
        "series": series,
        "remaining_uses": current_user.usage_count,
    }


@app.get("/users/me")
@limiter.limit("20/minute")
def read_users_me(request: Request, current_user: Annotated[models.User, Depends(auth.get_current_active_user)]):
    return {"username": current_user.username, "usage_count": current_user.usage_count}
