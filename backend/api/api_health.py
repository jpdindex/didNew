from fastapi import APIRouter, Depends

from backend.system.system_control import (
    Settings,
    get_settings,
    firebase_is_configured,
    firebase_is_initialized,
    firestore_is_ready,
    probe_firestore,
)
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    firebaseConfigured: bool
    firebaseInitialized: bool
    firestoreReady: bool

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    configured = firebase_is_configured(settings)
    if configured:
        probe_firestore()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        environment=settings.app_env,
        firebaseConfigured=configured,
        firebaseInitialized=firebase_is_initialized(),
        firestoreReady=firestore_is_ready(),
    )
