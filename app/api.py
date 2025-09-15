from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware

# slowapi imports
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import auth, models
from app.database import SessionLocal, engine, create_db_and_tables, get_db

# --- Rate Limiting Setup ---
# 创建一个 limiter 实例，key_func=get_remote_address 表示我们将基于 IP 地址进行限流
limiter = Limiter(key_func=get_remote_address)


# --- App Initialization ---
create_db_and_tables()

app = FastAPI()

# Set up CORS
origins = [
    "*",  # Allows all origins
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# 将 limiter 注册到 app state 中
app.state.limiter = limiter
# 添加异常处理器，当请求超过限制时，会返回 429 Too Many Requests 错误
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --- API Endpoints ---

@app.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute") # 每分钟最多 5 次
def register_user(request: Request, user: models.UserCreate, db: Session = Depends(get_db)):
    """用户注册端点"""
    db_user = auth.get_user(db, username=user.username)
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    hashed_password = auth.get_password_hash(user.password)
    db_user = models.User(username=user.username, hashed_password=hashed_password, usage_count=10)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return {"message": f"User {user.username} registered successfully"}


@app.post("/login", response_model=models.Token)
@limiter.limit("5/minute") # 每分钟最多 5 次
def login_for_access_token(request: Request, form_data: Annotated[OAuth2PasswordRequestForm, Depends()], db: Session = Depends(get_db)):
    """用户登录端点，成功后返回 JWT"""
    user = auth.get_user(db, username=form_data.username)
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = auth.create_access_token(
        data={"sub": user.username}
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/query")
@limiter.limit("20/minute") # 每分钟最多 20 次
def query_data(request: Request, current_user: Annotated[models.User, Depends(auth.get_current_active_user)], db: Session = Depends(get_db)):
    """
    受保护的查询端点。
    每次调用会消耗一次使用次数。
    """
    if current_user.usage_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usage limit exceeded. No more access attempts allowed."
        )
    
    current_user.usage_count -= 1
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    
    return {"message": "hello", "remaining_uses": current_user.usage_count}


@app.get("/users/me")
@limiter.limit("20/minute") # 每分钟最多 20 次
def read_users_me(request: Request, current_user: Annotated[models.User, Depends(auth.get_current_active_user)]):
    """获取当前用户信息（用于测试）"""
    return {"username": current_user.username, "usage_count": current_user.usage_count}