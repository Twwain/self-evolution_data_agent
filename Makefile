.PHONY: dev dev-backend dev-frontend build up down verify-caps

# ── 本地开发 ──
dev-backend:
	cd backend && uvicorn app.main:app --port 8001

dev-frontend:
	cd frontend && npm run dev

dev:
	@echo "请在两个终端分别执行 make dev-backend 和 make dev-frontend"

# ── Docker ──
build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

verify-caps:
	cd backend && python scripts/verify_mongo_caps.py
