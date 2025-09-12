# --- STAGE 1: Builder ---
# 使用与您环境匹配的 Python 版本作为基础镜像
FROM python:3.13-slim as builder

# 设置工作目录
WORKDIR /app

# 安装 uv，这是我们现代化的包管理器
RUN pip install uv

# 复制依赖定义文件
COPY pyproject.toml uv.lock ./

# 使用 uv 安装依赖项到系统环境中
# --system 在容器中是推荐做法，可以避免在容器内再创建一层 venv
RUN uv sync --system


# --- STAGE 2: Final Image ---
# 使用相同的基础镜像以保证兼容性
FROM python:3.13-slim

# 设置工作目录
WORKDIR /app

# 创建一个非 root 用户来运行应用，这是一个重要的安全实践
RUN useradd --create-home appuser

# 从 builder 阶段复制已安装的依赖项
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# 复制我们的应用代码
# 注意：因为 .dockerignore 的存在，本地的 .venv, .db 等文件不会被复制进来
COPY . .

# 将工作目录的所有权交给新创建的用户
RUN chown -R appuser:appuser /app

# 切换到非 root 用户
USER appuser

# 向 Docker 声明容器在运行时会监听的端口
EXPOSE 8000

# 容器启动时要执行的最终命令
# 我们直接用 python 运行启动器脚本
CMD ["python", "main.py"]
