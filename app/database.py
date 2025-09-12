
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 数据库文件将存放在项目根目录
DATABASE_URL = "sqlite:///./wow-auction.db"

# 创建 SQLAlchemy 引擎
# connect_args={"check_same_thread": False} 是 SQLite 特有的配置，允许多个线程访问同一个连接
# 在 FastAPI 中，不同的请求可能在不同的线程中处理，所以需要这个配置
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

# 创建一个数据库会话工厂
# autocommit=False 和 autoflush=False 确保事务需要被显式提交
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建一个所有模型类都会继承的基类
Base = declarative_base()

# 依赖项：获取数据库会话
# 这个函数将在每个请求中被调用，以提供一个独立的数据库会e话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 函数：创建数据库表
# 如果数据库文件不存在，这个函数会根据我们的模型定义创建所有的表
def create_db_and_tables():
    if not os.path.exists("./wow-auction.db"):
        print("Creating database and tables...")
        Base.metadata.create_all(bind=engine)
        print("Database and tables created.")
