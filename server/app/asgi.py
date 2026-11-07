"""Точка входа для uvicorn:  uvicorn app.asgi:app --host 127.0.0.1 --port 8000"""
from .main import create_app
from .wiring import Config, build_service

config = Config.from_env()
service = build_service(config)
app = create_app(service, config.token, dev=config.dev, background=True)
