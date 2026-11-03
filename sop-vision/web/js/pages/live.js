import { mountSafetyRecognition } from "./live-safety.js";
import { api } from "../api.js";
import { mountPreview } from "../preview.js";
import { icon } from "../icons.js";
import { store, subscribe, stationStatus, isOffline } from "../store.js";
import { badge, emptyBlock, errorBlock, STEP_STATUS, stationBadge, violationTag } from "../ui.js";
import { $, durHtml, esc, setHTML } from "../util.js";

const MODE = { all: "全部满足", any: "任一满足", seq: "依次满足" };

function railHtml(cfgSteps, cycle) {
  const live = new Map((cycle?.steps ?? []).map((s) => [s.id, s]));
  return cfgSteps.map((cs) => {
    const s = live.get(cs.id);
    const key = !s ? "pending" : s.early && s.status === "done" ? "early" : s.status;
    const def = STEP_STATUS[key];
    let time = "";
    if (s?.status === "done") time = durHtml(s.done_s - (s.armed_s ?? 0));
    else if (s?.status === "active") time = durHtml(cycle.elapsed_s - (s.armed_s ?? 0));
    const desc = cs.conds.join(" + ");
    return `<li class="rail-item rs-${key}">
      <span class="rail-ico tone-${def.tone}-text" title="${def.text}">${icon(def.icon, 16)}</span>
      <div class="rail-main">
        <div class="rail-name"><span class="ellipsis">${esc(cs.name)}</span><span class="rail-id mono">${esc(cs.id)}</span>${cs.optional ? '<span class="tag tone-muted">可选</span>' : ""}</div>
        <div class="rail-desc muted ellipsis" title="${esc(`${MODE[cs.mode]}：${desc}`)}">${esc(desc)}</div>
      </div>
      <div class="rail-state"><span class="rail-time num">${time || "—"}</span><span class="rail-text tone-${def.tone}-text">${def.text}</span></div>
    </li>`;
  }).join("");
}

function taktHtml(cycle, takt) {
  if (!cycle) return `<div class="takt-empty muted">等待工件到位后开始计时</div>`;
  const total = Math.max(takt || 0, cycle.elapsed_s * 1.04, 1);
  const w = (v) => `${((v / total) * 100).toFixed(2)}%`;
  const segs = cycle.steps.filter((s) => s.armed_s != null).map((s) => {
    const end = s.done_s ?? cycle.elapsed_s;
    const key = s.status === "active" ? "active" : s.early ? "early" : "done";
    return `<i class="tk tk-${key}" style="left:${w(s.armed_s)};width:${w(Math.max(end - s.armed_s, 0.15))}" title="${esc(s.name)}"></i>`;
  }).join("");
  const mark = takt ? `<i class="tk-takt" style="left:${w(takt)}"><b>节拍 ${takt} s</b></i>` : "";
  const over = takt && cycle.elapsed_s > takt ? `<i class="tk-over" style="left:${w(takt)};width:${w(cycle.elapsed_s - takt)}"></i>` : "";
  return `<div class="tk-track">${over}${segs}${mark}</div>`;
}

function violHtml(cycle) {
  const v = cycle?.violations ?? [];
  if (!v.length) return `<div class="inline-empty muted">${icon("check", 14)}${cycle ? "本周期暂无违规" : "无进行中的周期"}</div>`;
  return v.map((x) => `<div class="viol-item">${violationTag(x.kind)}<span class="ellipsis" title="${esc(x.message)}">${esc(x.message)}</span><span class="faint num">${x.ts.toFixed(1)} s</span></div>`).join("");
}

function zonesHtml(st) {
  const hands = new Set(st?.hands ?? []);
  const zs = st?.zones ?? {};
  const names = [...new Set([...Object.keys(zs), ...hands])];
  if (!names.length) return `<div class="inline-empty muted">暂无区域数据</div>`;
  return names.map((z) => `<span class="chip ${hands.has(z) ? "chip-hand" : ""}"><span class="mono">${esc(z)}</span>${zs[z] ? `<b>${esc(zs[z])}</b>` : ""}${hands.has(z) ? "<em>手</em>" : ""}</span>`).join("");
}

export function render(root, params) {
  const id = params.id || store.metas[0]?.id;
  if (!id) {
    root.innerHTML = emptyBlock("没有可监控的工位", "先用 python -m visual serve 配置文件.yaml 启动工位。");
    return () => {};
  }
  root.innerHTML = `<div class="live"><section class="panel skeleton-block" style="min-height:480px"></section><aside class="live-side"><section class="panel skeleton-block" style="min-height:360px"></section></aside></div>`;
  let off = () => {}, stopPreview = () => {}, dead = false;

  const load = async () => {
    let cfg;
    try {
      cfg = await api.config(id);
    } catch (e) {
      root.innerHTML = errorBlock(e.message);
      root.querySelector("[data-retry]").onclick = load;
      return;
    }
    if (dead) return;
    const meta = cfg.meta;
    root.innerHTML = `
      <div class="live">
        <section class="panel live-video">
          <header class="panel-head">
            <div class="head-left"><h2 class="ellipsis">${esc(meta.name)}</h2><span class="mono faint">${esc(meta.id)}</span>
              <label class="select-wrap"><span class="sr-only">切换工位</span><select id="sel">${store.metas.map((m) => `<option value="${esc(m.id)}" ${m.id === id ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></label></div>
            <span data-r="status"></span>
          </header>
          <div class="preview-tools"><span class="muted" data-preview-note role="status"></span><div class="head-left">
            <button class="btn btn-sm" data-preview-retry>WebRTC / 重试</button><button class="btn btn-sm" data-preview-analysis>分析画面</button></div></div>
          <div class="video-wrap"><img data-analysis src="${api.stream(id)}" alt="${esc(meta.name)} 分析画面">
            <video data-webrtc autoplay muted playsinline hidden aria-label="${esc(meta.name)} WebRTC 原始画面"></video>
            <div class="video-hud"><span class="hud-chip" data-r="cycle"></span><span class="hud-chip num" data-r="timer"></span></div></div>
          <div class="takt-panel"><div class="takt-head"><span>节拍进度</span><span class="num muted" data-r="taktText"></span></div><div data-r="takt"></div></div>
        </section>
        <aside class="live-side">
          <section class="panel"><header class="panel-head"><h2>步骤轨道</h2><span class="muted num" data-r="progress"></span></header><ol class="rail" data-r="rail"></ol></section>
          <section class="panel"><header class="panel-head"><h2>违规</h2></header><div class="panel-body viol" data-r="viol"></div></section>
          <section class="panel"><header class="panel-head"><h2>区域状态</h2><span class="muted">手 / 物件</span></header><div class="panel-body zone-chips" data-r="zones"></div></section>
          <section class="panel"><header class="panel-head"><h2>观测健康</h2><span class="muted">质量 / 融合</span></header><div class="panel-body"><div data-r="health"></div><div data-r="safety-recognition"></div></div></section>
        </aside>
      </div>`;
    $("#sel", root).onchange = (e) => (location.hash = `#/live/${encodeURIComponent(e.target.value)}`);
    stopPreview = mountPreview(root, id, cfg.preview);
    const r = (k) => root.querySelector(`[data-r="${k}"]`);
    const paintSafety = mountSafetyRecognition(r("safety-recognition"));
    const paint = () => {
      const st = store.states.get(id);
      paintSafety(st, store.conn === "open" && !isOffline(id));
      const c = st?.cycle;
      setHTML(r("status"), stationBadge(stationStatus(id)));
      setHTML(r("cycle"), c ? `周期 #${c.no}` : "等待工件");
      setHTML(r("timer"), c ? durHtml(c.elapsed_s) : "");
      setHTML(r("taktText"), meta.takt_s ? `${c ? c.elapsed_s.toFixed(1) : "0.0"} / ${meta.takt_s} s` : "未设置节拍");
      setHTML(r("takt"), taktHtml(c, meta.takt_s));
      const done = c?.steps.filter((s) => s.status === "done").length ?? 0;
      setHTML(r("progress"), `${done} / ${cfg.steps.length}`);
      setHTML(r("rail"), railHtml(cfg.steps, c));
      setHTML(r("viol"), violHtml(c));
      setHTML(r("zones"), zonesHtml(st));
      const q = st?.quality, fusion = st?.fusion;
      const fmt = (v, suffix = "") => Number.isFinite(v) ? `${v.toFixed(1)}${suffix}` : "—";
      setHTML(r("health"), `<div class="kv"><span>视觉门禁</span><b class="tone-${q?.degraded ? "warning" : q?.observation === "disabled" ? "muted" : "success"}-text">${q?.degraded ? "暂停取证" : q?.observation === "disabled" ? "未启用" : q ? "可取证" : "待观测"}</b></div>
        <div class="kv"><span>亮度漂移</span><span class="num">${fmt(q?.brightness_drift == null ? null : q.brightness_drift * 100, "%")}</span></div>
        <div class="kv"><span>背景可用</span><span class="num">${Number.isFinite(q?.background_pixels) ? q.background_pixels + " px" : "—"}</span></div>
        ${q?.reason ? `<div class="muted">${esc(q.reason)}</div>` : ""}
        <div class="kv"><span>有效设备信号</span><span class="num">${fusion?.signals?.filter((s) => s.quality === "ok").length ?? 0}</span></div>
        ${fusion?.conflicts?.length ? `<div class="tone-warning-text">来源冲突：${fusion.conflicts.map(esc).join("、")}</div>` : ""}
        ${fusion?.unknown?.length ? `<div class="muted">信号待核实：${fusion.unknown.map(esc).join("、")}</div>` : ""}`);
    };
    paint();
    off = subscribe((t, p) => { if (t === "state" ? p === id : t === "event" || t === "conn") paint(); });
  };
  load();
  return () => { dead = true; off(); stopPreview(); };
}
