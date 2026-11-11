"""浏览器项目范围：复用标注项目的 scene_id，不将未适配接口默认为全局。"""
from __future__ import annotations

import re

from fastapi import HTTPException

from .data_scope import scope_ids
from .labels import LabelError


def station_ids(user, labels):
    projects = scope_ids(user)
    if projects is None:
        return None
    stations = set()
    for pid in projects:
        try:
            scene = labels.get_project(pid).get('scene_id')
        except LabelError:
            continue
        if scene:
            stations.add(scene)
    return stations


def filter_stations(rows, allowed, key='station'):
    return list(rows) if allowed is None else [row for row in rows if row.get(key) in allowed]


def validate_projects(projects, labels):
    if projects is not None:
        for pid in projects:
            labels.get_project(pid)


async def check_request(request, user, manager):
    """角色检查在主认证中间件完成；本层只检查资源归属。"""
    allowed = scope_ids(user)
    if allowed is None:
        return
    path, method = request.url.path, request.method
    if path == '/api/cross-camera/groups' or path.startswith('/api/cross-camera/groups/'):
        return  # Router checks every member before exposing or changing a group.
    if method == 'GET' and path in {
        '/api/label-projects', '/api/label-engines', '/api/models',
        '/api/training/capabilities', '/api/applications', '/api/anomaly/scenes',
        '/api/stations', '/api/alerts', '/api/alerts/stats', '/api/alerts/false-positives',
    }:
        return  # 列表端点自身过滤；模型为共享运行资产。
    if method == 'POST' and re.fullmatch(r'/api/models/[^/]+/trial', path):
        return  # 单次上传试跑，不读取其他项目图片，也不写项目。
    match = re.fullmatch(r'/api/(?:applications|anomaly)/([^/]+)(?:/.*)?', path)
    if match:
        if match[1] in station_ids(user, manager.labels):
            return
        raise HTTPException(403, '应用不在用户数据范围')
    match = re.fullmatch(r'/api/stations/([^/]+)(?:/.*)?', path)
    if match:
        if match[1] in station_ids(user, manager.labels):
            return
        raise HTTPException(403, '工位不在用户数据范围')
    match = re.fullmatch(r'/api/alerts/(\d+)', path)
    if match and method == 'PATCH':
        row = manager.alerts.get(int(match[1]))
        if row and row['station'] in station_ids(user, manager.labels):
            return
        raise HTTPException(403, '告警不在用户数据范围')
    project = None
    match = re.fullmatch(r'/api/label-projects/([^/]+)/(?:assets|import-dataset|quality|training|versions(?:/[^/]+/export.zip)?)', path)
    if match:
        project = match[1]
    match = re.fullmatch(r'/api/label-assets/([^/]+)/(?:annotations|confirm|revisions|restore|image)', path)
    if match:
        project = manager.labels.get_asset(match[1])['project_id']
    match = re.fullmatch(r'/api/label-jobs/([^/]+)(?:/cancel)?', path)
    if match:
        project = manager.label_jobs.get(match[1])['project_id']
    match = re.fullmatch(r'/api/training/jobs/([^/]+)(?:/cancel)?', path)
    if match:
        project = manager.training.get(match[1])['project_id']
    if path == '/api/label-jobs':
        if method == 'GET':
            project = request.query_params.get('project_id')
        elif method == 'POST':
            try:
                body = await request.json()
                project = body.get('project_id') if isinstance(body, dict) else None
            except ValueError:
                raise HTTPException(400, '标注任务JSON无效') from None
    if project is not None and project in allowed:
        return
    raise HTTPException(403, '此资源未开放给限定项目范围的用户')
