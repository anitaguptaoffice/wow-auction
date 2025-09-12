
from sqlalchemy import Column, Integer, String
from pydantic import BaseModel
from app.database import Base

# SQLAlchemy 模型：代表数据库中的 'users' 表
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    usage_count = Column(Integer, default=10) # 默认为10次

# Pydantic 模型：用于 API 的数据校验和响应

# 创建用户时请求体的数据模型
class UserCreate(BaseModel):
    username: str
    password: str

# 登录时请求体的数据模型
class UserLogin(BaseModel):
    username: str
    password: str

# 返回给客户端的 Token 模型
class Token(BaseModel):
    access_token: str
    token_type: str

# 在 Token 中携带的数据模型
class TokenData(BaseModel):
    username: str | None = None
