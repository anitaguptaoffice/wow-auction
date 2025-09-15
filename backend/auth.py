
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from backend import models
from backend.database import get_db

# --- 安全配置 ---
# 从环境变量中读取 SECRET_KEY，如果不存在，则使用一个默认的、不安全的 key（仅用于开发）
SECRET_KEY = os.getenv("SECRET_KEY", "a-very-insecure-default-key-for-dev-only")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# 如果使用的是不安全的默认密钥，则在启动时打印警告
if SECRET_KEY == "a-very-insecure-default-key-for-dev-only":
    print("\033[93m" + "警告: 正在使用默认的开发密钥。请在生产环境中通过环境变量设置一个安全的 SECRET_KEY。" + "\033[0m")

# --- 密码哈希 ---
# 使用 bcrypt 算法来处理密码
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- OAuth2 --- 
# 定义 OAuth2 密码模式的 URL，FastAPI 将使用它来从请求中提取 token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

# --- 认证函数 ---

def verify_password(plain_password, hashed_password):
    """验证明文密码和哈希密码是否匹配"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    """生成密码的哈希值"""
    return pwd_context.hash(password)

def create_access_token(data: dict):
    """根据给定的数据创建一个新的 JWT access token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_user(db: Session, username: str):
    """从数据库中按用户名获取用户"""
    return db.query(models.User).filter(models.User.username == username).first()


# --- FastAPI 依赖项：获取当前用户 ---

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: Session = Depends(get_db)):
    """
    解析和验证 JWT，返回当前用户。
    如果 token 无效或用户不存在，则抛出 HTTP 异常。
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = models.TokenData(username=username)
    except JWTError:
        raise credentials_exception
    
    user = get_user(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

# FastAPI 依赖项：获取当前活跃用户（可以扩展此函数以检查用户是否被禁用等）
async def get_current_active_user(current_user: Annotated[models.User, Depends(get_current_user)]):
    """一个简单的依赖项，可以在未来扩展以检查用户状态"""
    # if current_user.disabled:
    #     raise HTTPException(status_code=400, detail="Inactive user")
    return current_user
