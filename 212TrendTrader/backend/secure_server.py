import os
import secrets
from fastapi import Request
from fastapi.responses import JSONResponse
from server import app

API_TOKEN = os.getenv("BACKEND_API_TOKEN", "")
PUBLIC_PATHS = {"/health"}

@app.middleware("http")
async def require_backend_token(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    supplied = request.headers.get("X-API-Key", "")
    if not API_TOKEN or not secrets.compare_digest(supplied, API_TOKEN):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)
