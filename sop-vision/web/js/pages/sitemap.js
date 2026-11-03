import { api } from "../api.js";
import { icon } from "../icons.js";
import { emptyBlock, errorBlock, toast } from "../ui.js";
import { $, esc, setHTML } from "../util.js";

/** 厂区图页:平面图(图片底图+打点)与经纬度地图(GeoBody)双 tab;告警点位闪红。 */
let poll = null;

function pointSvg(points, w, h, editing, selId) {
  const dots = points.map((p) => {
    const x = p.x * w, y = p.y * h;
    const color = p.kind === "camera" ? "#3ec6c1" : p.kind === "station" ? "#59c58d" : "#d8ac52";
    return `<g class="pt ${p.id === selId ? "sel" : ""}" data-pt="${esc(p.id)}" tabindex="0" role="button" aria-label="${esc(p.name)}">
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="9" fill="${color}" stroke="#0b1114" stroke-width="2"/>
      <text x="${(x + 12).toFixed(1)}" y="${(y + 4).toFixed(1)}">${esc(p.name)}</text></g>`;
  }).join("");
  return `<svg class="plan-svg ${editing ? "edit" : ""}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet">${dots}</svg>`;
}

export function render(root) {
  root.innerHTML = `
    <div class="filters">
      <div class="seg-ctl" role="tablist">
        <button class="on" data-tab="plan">厂区平面图</button>
        <button data-tab="geo">经纬度地图</button>
      </div>
      <button class="btn btn-primary" id="m-upload">${icon("check", 14)}上传平面图</button>
      <input type="file" id="m-file" accept="image/png,image/jpeg" hidden>
      <label class="field" id="m-name-wrap" style="display:none"><span>名称</span><input id="m-name" placeholder="如:一号厂房"></label>
      <button class="btn" id="m-refresh">${icon("refresh", 14)}刷新</button>
    </div>
    <section class="panel">
      <header class="panel-head"><div class="head-left"><h2 data-r="title">厂区图</h2>
        <span class="muted">点击底图打摄像头/工位点位;红色脉冲=有待处理告警;3D 按钮为透视预览(伪 3D,轻量)</span></div>
        <button class="btn btn-sm" id="m-3d">${icon("palette", 14)}3D 视角</button></header>
      <div class="panel-body" data-r="body">
        <div class="skeleton-block" style="height:420px"></div>
      </div>
    </section>`;

  const body = $("[data-r='body']", root);
  let tab = "plan", plan = null, editing = false, selPt = null, pendingStations = new Set(), dead = false;

  const flash = async () => {
    try {
      pendingStations = new Set((await api.alerts({ status: "pending", limit: 300 }))
        .map((a) => a.station));
    } catch { /* 轮询失败静默,下轮再试 */ }
  };

  const paintPlan = async () => {
    const list = await api.plansList();
    if (!list.length) {
      setHTML(body, emptyBlock("还没有平面图", "点「上传平面图」导入厂区图(CAD 请先导出 PNG/JPG)。"));
      return;
    }
    plan = plan && list.find((x) => x.id === plan.id) ? list.find((x) => x.id === plan.id) : list[0];
    const img = new Image();
    img.src = `/api/plans/${encodeURIComponent(plan.id)}/image`;
    await img.decode().catch(() => {});
    const w = 1000, h = Math.round(1000 * (img.height / img.width)) || 500;
    const pts = (plan.points ?? []).map((p) => ({
      ...p, alert: pendingStations.has(p.name) || pendingStations.has(p.id),
    }));
    setHTML(body, `<div class="plan-wrap ${tab === "plan" ? "" : "hidden"}">
      <div class="plan-canvas ${root.dataset.mode3d ? "mode3d" : ""}"><div data-r="svg" style="position:relative;width:100%">
        <img src="/api/plans/${encodeURIComponent(plan.id)}/image" style="width:100%;display:block;border-radius:6px" draggable="false">
        ${pointSvg(pts, w, h, editing, selPt)}
      </div></div>
      <div class="plan-side"><b>点位</b>
        ${pts.map((p) => `<div class="kv"><span>${esc(p.name)} <span class="faint">(${esc(p.kind)})</span></span>
          <button class="icon-btn" data-del="${esc(p.id)}" aria-label="删除点位">${icon("cross", 13)}</button></div>`).join("")
          || '<span class="muted">点击底图添加点位</span>'}
      </div>
    </div>`);
    const svg = $("[data-r='svg'] svg", body);
    svg.addEventListener("click", async (e) => {
      const dot = e.target.closest("[data-pt]");
      if (dot && !editing) { selPt = selPt === dot.dataset.pt ? null : dot.dataset.pt; return paintPlan(); }
      if (!editing) return;
      const r = svg.getBoundingClientRect();
      const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      const name = prompt("点位名称(如:东门相机):", `点位${pts.length + 1}`);
      if (!name) return;
      try {
        await api.sitePoint(plan.id, name, "camera", +x.toFixed(4), +y.toFixed(4));
        selPt = null;
        await paintPlan();
        toast("点位已保存", "success");
      } catch (err) { toast(err.message, "error"); }
    });
    root.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      await api.sitePointDelete(b.dataset.del);
      await paintPlan();
    }));
  };

  const paintGeo = async () => {
    try {
      const m = await fetch("/api/geo/map").then((r) => r.json());
      const pts = m.points ?? [];
      if (!pts.length) {
        setHTML(body, emptyBlock("还没有经纬度点位", "GB28181 目录同步或 ONVIF 读取后自动出现;也可用 API 手动添加。"));
        return;
      }
      const minLat = Math.min(...pts.map((p) => p.lat)) - 0.002, maxLat = Math.max(...pts.map((p) => p.lat)) + 0.002;
      const minLng = Math.min(...pts.map((p) => p.lng)) - 0.002, maxLng = Math.max(...pts.map((p) => p.lng)) + 0.002;
      const px = (p) => ((p.lng - minLng) / (maxLng - minLng)) * 900 + 50;
      const py = (p) => (1 - (p.lat - minLat) / (maxLat - minLat)) * 420 + 40;
      setHTML(body, `<svg viewBox="0 0 1000 500" style="width:100%">
        ${pts.map((p) => {
          const alert = pendingStations.has(p.id) || pendingStations.has(p.name);
          return `<g><circle cx="${px(p).toFixed(0)}" cy="${py(p).toFixed(0)}" r="${alert ? 12 : 8}"
            fill="${alert ? "#e27478" : "#3ec6c1"}" stroke="#0b1114" stroke-width="2"/>
            <text x="${px(p) + 14}" y="${py(p) + 4}" fill="#a7b2b8">${esc(p.name)}${alert ? " ⚠" : ""}</text></g>`;
        }).join("")}
      </svg>`);
    } catch (e) {
      setHTML(body, errorBlock(e.message));
    }
  };

  const paint = async () => {
    await flash();
    if (tab === "plan") return paintPlan();
    return paintGeo();
  };

  root.querySelector(".seg-ctl").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    tab = b.dataset.tab;
    root.querySelectorAll(".seg-ctl button").forEach((x) => x.classList.toggle("on", x === b));
    plan = null;
    paint();
  };
  $("#m-upload", root).onclick = () => $("#m-file", root).click();
  $("#m-file", root).onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const name = $("#m-name", root).value.trim() || f.name.replace(/\.\w+$/, "");
    const fd = new FormData();
    fd.append("name", name);
    fd.append("image", f);
    try {
      await fetch("/api/plans", { method: "POST", body: fd }).then((r) => { if (!r.ok) throw new Error("上传失败"); });
      toast("平面图已上传", "success");
      plan = null;
      paint();
    } catch (err) { toast(err.message, "error"); }
  };
  $("#m-refresh", root).onclick = paint;
  $("#m-3d", root).onclick = () => {
    root.dataset.mode3d = root.dataset.mode3d ? "" : "1";
    const el = body.querySelector(".plan-canvas");
    if (el) el.classList.toggle("mode3d");
    toast(root.dataset.mode3d ? "伪 3D 视角开(透视预览)" : "伪 3D 视角关", "info");
  };
  $("#m-name-wrap", root).style.display = "";
  paint();
  poll = setInterval(flash, 5000);
  return () => { if (poll) clearInterval(poll); dead = true; void dead; };
}
