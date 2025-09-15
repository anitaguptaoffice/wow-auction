# --- STAGE 1: Builder ---
# 使用与您环境匹配的 Python 版本作为基础镜像
FROM python:3.13-slim AS builder

# 设置工作目录
WORKDIR /app

# 安装 uv
RUN pip install uv

# 复制依赖定义文件
COPY pyproject.toml uv.lock ./

# 创建一个虚拟环境
RUN uv venv

# 将依赖项安装到该虚拟环境中
# uv sync 会自动检测并使用当前目录下的 .venv
RUN uv sync


# --- STAGE 2: Final Image ---
# 使用相同的基础镜像以保证兼容性
FROM python:3.13-slim

# 设置工作目录
WORKDIR /app

# 创建一个非 root 用户来运行应用
RUN useradd --create-home appuser

# 从 builder 阶段复制已安装好依赖的虚拟环境
COPY --from=builder /app/.venv ./.venv

# 复制我们的应用代码
COPY . .

# 将工作目录的所有权交给新创建的用户
RUN chown -R appuser:appuser /app

# 切换到非 root 用户
USER appuser

# 声明容器在运行时会监听的端口
EXPOSE 8000

# 容器启动时要执行的最终命令
# 我们使用 .venv 中的 python 解释器来运行启动器脚本
CMD ["./.venv/bin/python", "main.py"]