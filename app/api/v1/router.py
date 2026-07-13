from fastapi import APIRouter

from app.api.v1 import analytics, runs

api_router = APIRouter(prefix="/v1")
api_router.include_router(runs.router)
api_router.include_router(analytics.router)
