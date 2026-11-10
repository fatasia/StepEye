"""One-click training endpoints; model/data selection uses registered resource IDs."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field


class TrainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=100)


def router(jobs):
    api = APIRouter()

    @api.get("/api/training/capabilities")
    def capabilities():
        return jobs.capabilities()

    @api.get("/api/label-projects/{pid}/training")
    def listing(pid: str):
        return jobs.list(pid)

    @api.post("/api/label-projects/{pid}/training", status_code=202)
    def submit(pid: str, body: TrainBody, request: Request):
        return jobs.submit(pid, body.engine_id, body.idempotency_key, request.state.user["username"])

    @api.get("/api/training/jobs/{jid}")
    def detail(jid: str):
        return jobs.get(jid)

    @api.post("/api/training/jobs/{jid}/cancel")
    def cancel(jid: str):
        return jobs.cancel(jid)

    return api
