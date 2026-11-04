"""Cross-camera group endpoints with all-member scope checks."""
from fastapi import APIRouter, HTTPException, Request

from .cross_camera import GroupBody


def router(store, allowed_stations):
    api = APIRouter()

    def ensure(request, members=(), *, write=False):
        user = getattr(request.state, 'user', None)
        if user is None:
            raise HTTPException(401, '未登录')
        if user.get('role') not in ({'admin'} if write else {'viewer', 'operator', 'admin'}):
            raise HTTPException(403, '权限不足')
        allowed = allowed_stations(user)
        if allowed is not None and any(member.station not in allowed for member in members):
            raise HTTPException(403, '组内工位不在用户数据范围')
        return allowed

    def get(group_id):
        try:
            return store.get(group_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @api.get('/api/cross-camera/groups')
    def listing(request: Request):
        allowed = ensure(request)
        return [row for row in store.list() if allowed is None or
                all(member['station'] in allowed for member in row['members'])]

    @api.put('/api/cross-camera/groups/{group_id}')
    def save(group_id: str, body: GroupBody, request: Request):
        ensure(request, body.members, write=True)
        try:
            return store.save(group_id, body, authorize_previous=lambda previous:
                              ensure(request, previous.members, write=True))
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.delete('/api/cross-camera/groups/{group_id}')
    def delete(group_id: str, request: Request):
        ensure(request, write=True)
        try:
            return store.delete(group_id, authorize=lambda previous:
                                ensure(request, previous.members, write=True))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @api.get('/api/cross-camera/groups/{group_id}')
    def detail(group_id: str, request: Request):
        body = get(group_id)
        ensure(request, body.members)
        return {'id': group_id, **body.model_dump(mode='json')}

    @api.get('/api/cross-camera/groups/{group_id}/snapshot')
    def snapshot(group_id: str, request: Request):
        body = get(group_id)
        ensure(request, body.members)
        return store.snapshot(group_id, body=body)

    return api
