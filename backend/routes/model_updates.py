"""Manual model update check, install, rollback."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import model_updates

router = APIRouter()


class VersionRef(BaseModel):
    repo: str = Field(..., max_length=200)
    commit: str = Field(..., pattern="^[0-9a-f]{40}$")


def _guard(fn, *args):
    try:
        return fn(*args)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/model-updates")
async def list_versions() -> list[dict[str, Any]]:
    return model_updates.list_models()


@router.post("/model-updates/check")
async def check() -> list[dict[str, Any]]:
    return await model_updates.check_updates()


@router.post("/model-updates/install")
async def install(ref: VersionRef) -> dict[str, Any]:
    return _guard(model_updates.start_update, ref.repo, ref.commit)


@router.post("/model-updates/activate")
async def activate(ref: VersionRef) -> list[dict[str, Any]]:
    try:
        return await model_updates.activate(ref.repo, ref.commit)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/model-updates/delete")
async def delete(ref: VersionRef) -> list[dict[str, Any]]:
    return _guard(model_updates.delete_version, ref.repo, ref.commit)
