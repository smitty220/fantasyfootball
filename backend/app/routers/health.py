from fastapi import APIRouter

router = APIRouter()

APP_VERSION = "0.1.0"


@router.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}
