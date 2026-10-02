"""WAN Lab Manager — FastAPI backend.

Serves the isometric topology UI, persists the topology configuration,
proxies every VyOS interaction (API keys never reach the browser) and
keeps a background poller that reconciles router state.
"""
import json
import os
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import config, models, storage, vyos
from .state import poller

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(_: FastAPI):
    poller.set_topology(storage.load())
    poller.start()
    yield
    await poller.stop()
    await vyos.close()


app = FastAPI(title="WAN Lab Manager", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Optional token auth for mutating endpoints
# ---------------------------------------------------------------------------
def require_token(x_auth_token: str = Header(default="")):
    if config.API_TOKEN and x_auth_token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing token (X-Auth-Token)")


# ---------------------------------------------------------------------------
# Topology configuration
# ---------------------------------------------------------------------------
@app.get("/api/topology")
async def get_topology():
    return models.redact(poller.topology)


@app.put("/api/topology", dependencies=[Depends(require_token)])
async def put_topology(data: dict = Body(...)):
    data = models.merge_secrets(data, poller.topology)
    try:
        data = models.validate_topology(data)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid topology: {exc}") from exc
    await storage.save(data)
    poller.set_topology(data)
    return models.redact(data)


@app.get("/api/topology/export")
async def export_topology(include_secrets: bool = False,
                          x_auth_token: str = Header(default="")):
    data = poller.topology
    if include_secrets:
        if config.API_TOKEN and x_auth_token != config.API_TOKEN:
            raise HTTPException(status_code=401, detail="export with secrets requires X-Auth-Token")
    else:
        data = models.redact(data)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="topology.json"'},
    )


@app.get("/api/topology/history")
async def get_history():
    return storage.list_history()


@app.post("/api/topology/restore/{name}", dependencies=[Depends(require_token)])
async def restore_history(name: str):
    try:
        data = await storage.restore(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup not found") from exc
    poller.set_topology(data)
    return models.redact(data)


# ---------------------------------------------------------------------------
# Live state / actions
# ---------------------------------------------------------------------------
@app.get("/api/state")
async def get_state():
    return poller.state


class ApplyBody(BaseModel):
    enabled: bool = True
    profile: str
    persist: bool = False


@app.post("/api/links/{link_id}/apply", dependencies=[Depends(require_token)])
async def apply_link(link_id: str, body: ApplyBody):
    link = next((l for l in poller.topology.get("links", []) if l.get("id") == link_id), None)
    if not link:
        raise HTTPException(status_code=404, detail=f"link '{link_id}' not found")
    profiles = poller.topology.get("linkProfiles", {}) or {}
    if body.profile not in profiles:
        raise HTTPException(status_code=422, detail=f"profile '{body.profile}' does not exist in linkProfiles")
    try:
        lines = await poller.apply_link(link, body.enabled, body.profile, body.persist)
    except vyos.VyOSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"lines": lines, "state": poller.state["links"].get(link_id)}


@app.post("/api/test", dependencies=[Depends(require_token)])
async def run_tests():
    return {"lines": await poller.run_tests()}


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "routers": poller.state.get("routers", {}),
            "lastPoll": poller.state.get("updatedAt")}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(404)
async def not_found(request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
