# =============================================================
# TianJun Fleet Hub 容器镜像 (多阶段: node 构建前端 → python 运行)
#
# 构建 (在 hub/ 目录下):
#   docker build -f deploy/Dockerfile -t tianjun-hub .
# 运行:
#   docker run -d --name tianjun-hub -p 9100:9100 \
#     -e HUB_ADMIN_PASSWORD='强口令' \
#     -v tianjun-hub-data:/data tianjun-hub
# 或直接用 deploy/docker-compose.yml。
# =============================================================

# ---- 阶段 1: 前端构建 ----
FROM node:20-slim AS webbuild
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 阶段 2: 运行时 ----
FROM python:3.10-slim
WORKDIR /app

COPY requirements.txt /app/hub/requirements.txt
RUN pip install --no-cache-dir -r /app/hub/requirements.txt

COPY backend/ /app/hub/backend/
COPY README.md /app/hub/README.md
COPY --from=webbuild /build/dist /app/hub/frontend/dist
RUN touch /app/hub/__init__.py

# 数据目录挂卷 (hub.db + Fernet 密钥; 备份挂载点即备份枢纽)
ENV HUB_DATA_DIR=/data
VOLUME /data

EXPOSE 9100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:9100/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "hub.backend.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "9100"]
