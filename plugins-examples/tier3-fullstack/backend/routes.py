"""插件 FastAPI router 示例。"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    return {
        "success": True,
        "plugin": "internal-demo-fullstack",
        "status": "ok",
    }


@router.get("/config-preview")
def config_preview():
    return {
        "success": True,
        "keys": [
            "plugin.internal-demo.enabled",
            "plugin.internal-demo.note_prefix",
        ],
    }
