.PHONY: dev dev-backend dev-frontend \
        prod-build prod-up prod-down prod-logs prod-ps \
        build up down verify-caps

# ── 本地开发 (dev: 裸跑, 不进 docker) ──
dev-backend:
	cd backend && uvicorn app.main:app --port 8001

dev-frontend:
	cd frontend && npm run dev

dev:
	@echo "请在两个终端分别执行 make dev-backend 和 make dev-frontend"

# ── 生产部署 (prod: docker-compose base + override) ──
# --env-file: 同一文件供 ① compose 插值 (IS_CHROMA_HOST_PATH) ② 容器 env 注入 (见 design AD-7)
COMPOSE_PROD := docker compose --env-file backend/.env.prod -f docker-compose.yml -f docker-compose.prod.yml

prod-build:
	$(COMPOSE_PROD) build

prod-up:
	$(COMPOSE_PROD) up -d

prod-down:
	$(COMPOSE_PROD) down

prod-logs:
	$(COMPOSE_PROD) logs -f

prod-ps:
	$(COMPOSE_PROD) ps

# ── 兼容别名 (build/up/down → prod) ──
build: prod-build
up: prod-up
down: prod-down

# ── 工具 ──
verify-caps:
	cd backend && python scripts/verify_mongo_caps.py
