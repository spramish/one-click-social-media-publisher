"""Aggregate router for API v1.

New endpoint modules (accounts, media, posts, ...) are registered here as
later phases are implemented.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    accounts,
    ai,
    ai_models,
    analytics,
    audit,
    auth,
    health,
    media,
    notifications,
    posts,
    scheduling,
    workspaces,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
api_router.include_router(media.router, prefix="/media", tags=["media"])
# Scheduling shares the ``/posts`` prefix and must be registered *before* the
# posts router: ``GET /posts/{workspace_id}/schedule`` would otherwise be matched
# by ``GET /posts/{workspace_id}/{post_id}`` and rejected as an invalid UUID.
api_router.include_router(scheduling.router, prefix="/posts", tags=["scheduling"])
api_router.include_router(posts.router, prefix="/posts", tags=["posts"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(ai_models.router, prefix="/ai", tags=["ai-models"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(
    notifications.router, prefix="/notifications", tags=["notifications"]
)
api_router.include_router(audit.router, prefix="/workspaces", tags=["audit"])
