export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

async function request(url, opts) {
  let res;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new ApiError("无法连接服务。请确认 visual 服务正在运行，然后点击重试。");
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).detail ?? "";
    } catch { /* 非 JSON 响应 */ }
    throw new ApiError(detail || `请求失败（HTTP ${res.status}）。请稍后重试。`, res.status);
  }
  return res.json();
}

export const api = {
  stations: () => request("/api/stations"),
  config: (id) => request(`/api/stations/${encodeURIComponent(id)}/config`),
  cycles: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
    return request(`/api/cycles?${q}`);
  },
  cycle: (id) => request(`/api/cycles/${encodeURIComponent(id)}`),
  trend: (hours = 24) => request(`/api/trend?hours=${hours}`),
  calibrate: (id, zones) =>
    request(`/api/stations/${encodeURIComponent(id)}/calibrate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zones }),
    }),
  saveZones: (id, zones) =>
    request(`/api/stations/${encodeURIComponent(id)}/zones`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zones }),
    }),
  templates: () => request("/api/templates"),
  stationTemplate: (tid, params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
    return request(`/api/templates/station/${encodeURIComponent(tid)}?${q}`);
  },
  stream: (id) => `/api/stations/${encodeURIComponent(id)}/stream.mjpg`,
  snapshot: (id, raw = true) => `/api/stations/${encodeURIComponent(id)}/snapshot.jpg?raw=${raw}&t=${Date.now()}`,
  frame: (cid, name) => `/api/cycles/${encodeURIComponent(cid)}/frames/${encodeURIComponent(name)}`,
  inspectionScenarios: () => request("/api/inspection/scenarios"),
  inspectionSubmit: (scenarioId, files) => {
    const fd = new FormData();
    fd.append("scenario_id", scenarioId);
    files.forEach((f) => fd.append("files", f));
    return request("/api/inspection/tasks", { method: "POST", body: fd });
  },
  inspectionTasks: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
    return request(`/api/inspection/tasks?${q}`);
  },
  inspectionTask: (id) => request(`/api/inspection/tasks/${encodeURIComponent(id)}`),
  inspectionImage: (id, name) => `/api/inspection/tasks/${encodeURIComponent(id)}/images/${encodeURIComponent(name)}`,
  login: (username, password) =>
    request("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify({ username, password }) }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  me: () => request("/api/auth/me"),
  alerts: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
    return request(`/api/alerts?${q}`);
  },
  alertStats: () => request("/api/alerts/stats"),
  inspectionStats: () => request("/api/inspection/stats"),
  alertPatch: (id, body) =>
    request(`/api/alerts/${encodeURIComponent(id)}`, { method: "PATCH",
                                                       headers: { "Content-Type": "application/json" },
                                                       body: JSON.stringify(body) }),
  sampleGenerate: (ref, label, engine, n, description) => {
    const fd = new FormData();
    fd.append("ref", ref); fd.append("label", label); fd.append("engine", engine);
    fd.append("n", String(n)); fd.append("description", description);
    return request("/api/samples/generate", { method: "POST", body: fd });
  },
  sampleManifest: (limit = 100) => request(`/api/samples/manifest?limit=${limit}`),
  sampleImage: (name) => `/api/samples/images/${encodeURIComponent(name)}`,
  timelineCreate: (name, file) => {
    const fd = new FormData();
    fd.append("name", name); fd.append("video", file);
    return request("/api/timelines", { method: "POST", body: fd });
  },
  timelineList: () => request("/api/timelines"),
  timelineIntervals: (id, intervals) =>
    request(`/api/timelines/${encodeURIComponent(id)}/intervals`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ intervals }) }),
  timelineFrame: (id, name) => `/api/timelines/${encodeURIComponent(id)}/frames/${encodeURIComponent(name)}`,
  timelineVideo: (id) => `/api/timelines/${encodeURIComponent(id)}/video`,
  shadowSamples: (unlabeled = true) => request(`/api/shadow/samples?unlabeled=${unlabeled ? 1 : 0}`),
  plansList: () => request("/api/plans"),
  sitePoint: (planId, name, kind, x, y) =>
    request(`/api/plans/${encodeURIComponent(planId)}/points`, { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, kind, x, y }) }),
  sitePointDelete: (ptid) => request(`/api/plans/${encodeURIComponent(ptid)}`, { method: "DELETE" }),
  shadowTruth: (id, truth) =>
    request(`/api/shadow/${encodeURIComponent(id)}/truth`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ truth }) }),
};
