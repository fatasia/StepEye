/** 全局状态：工位快照、告警流、连接状态。页面通过 subscribe 订阅变化。 */
import { api } from "./api.js";

const OFFLINE_AFTER_MS = 5000;
const MAX_ALERTS = 60;

export const store = {
  metas: [],
  states: new Map(),
  seen: new Map(),
  alerts: [],
  conn: "connecting", // connecting | open | retry
};

const listeners = new Set();
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
const emit = (type, payload) => listeners.forEach((fn) => fn(type, payload));

export function isOffline(id) {
  const t = store.seen.get(id);
  return t == null || Date.now() - t > OFFLINE_AFTER_MS;
}

/** 工位状态：告警 > 预警 > 作业中 > 空闲；长时间无数据视为离线。 */
export function stationStatus(id) {
  if (isOffline(id)) return "offline";
  const st = store.states.get(id);
  const c = st?.cycle;
  if (c?.violations.some((v) => v.severity === "error")) return "alarm";
  if (st?.quality?.degraded || c?.violations.length) return "warn";
  return c ? "running" : "idle";
}

function ingestState(id, data) {
  store.states.set(id, data);
  store.seen.set(id, Date.now());
  if (!store.metas.some((m) => m.id === id) && data.meta) store.metas.push(data.meta);
}

function pushAlert(a) {
  store.alerts.unshift(a);
  store.alerts.length = Math.min(store.alerts.length, MAX_ALERTS);
}

export async function bootstrap() {
  const list = await api.stations();
  for (const st of list) {
    if (!st.meta) continue;
    ingestState(st.meta.id, st);
    for (const a of [...(st.alerts || [])].reverse()) {
      pushAlert({ ...a, station: st.meta.id, at: a.at ?? Date.now() });
    }
  }
  store.metas = list.map((s) => s.meta).filter(Boolean);
  emit("ready");
}

let retry = 1000;
export function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => {
    store.conn = "open";
    retry = 1000;
    emit("conn");
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "hello") {
      store.metas = msg.stations;
      emit("ready");
    } else if (msg.type === "state") {
      ingestState(msg.station, msg.data);
      emit("state", msg.station);
    } else if (msg.type === "event") {
      if (msg.kind === "violation") {
        const d = msg.data;
        pushAlert({ station: msg.station, cycle_id: d.cycle_id, kind: d.kind, severity: d.severity, step: d.step, message: d.message, at: Date.now() });
      }
      emit("event", msg);
    }
  };
  ws.onclose = () => {
    store.conn = "retry";
    emit("conn");
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 8000);
  };
}
