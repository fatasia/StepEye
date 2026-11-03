"""Recipe management routes; writes use the server's common RBAC middleware."""
from fastapi import APIRouter, HTTPException, Request
from .recipes import RecipeBody, VersionBody, SwitchBody


def router(service):
    api = APIRouter()

    def call(fn, *args):
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def actor(request):
        return request.state.user["username"]

    @api.get("/api/recipes")
    def listing():
        return service.store.list()

    @api.post("/api/recipes", status_code=201)
    def create(body: RecipeBody, request: Request):
        return call(service.create, body, actor(request))

    @api.get("/api/recipes/{rid}/versions")
    def versions(rid: str):
        return call(service.store.versions, rid)

    @api.post("/api/recipes/{rid}/versions", status_code=201)
    def version(rid: str, body: VersionBody, request: Request):
        return call(service.add_version, rid, body, actor(request))

    @api.get("/api/stations/{sid}/recipe")
    def status(sid: str):
        return call(service.status, sid)

    @api.post("/api/stations/{sid}/recipe")
    def switch(sid: str, body: SwitchBody, request: Request):
        return call(service.switch, sid, body.version_id, actor(request))

    return api
