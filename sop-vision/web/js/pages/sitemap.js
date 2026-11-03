import { api } from "../api.js";
import { icon } from "../icons.js";
import { emptyBlock, errorBlock, toast } from "../ui.js";
import { $, esc, fmtTime, setHTML } from "../util.js";
import { store, subscribe, isOffline } from "../store.js";
import { twinScene } from "../twin-scene.js";

const KIND = { camera: "摄像头", station: "工位", sensor: "传感器" };

export function render(root) {
  root.innerHTML = `<div class="twin-workspace">
    <header class="twin-page-head"><div><span class="twin-eyebrow">空间态势 / 厂区图</span><h2>让每个风险，都有位置。</h2></div>
      <div class="twin-head-tools"><div class="seg-ctl" role="tablist" aria-label="地图类型"><button class="on" role="tab" aria-selected="true" data-tab="plan">厂区空间</button><button role="tab" aria-selected="false" data-tab="geo">经纬度点位</button></div>
      <label class="field" data-plan-only><span>底图</span><select id="m-plan" aria-label="选择平面图"><option value="">正在加载…</option></select></label>
      <button class="btn btn-primary" id="m-upload" data-plan-only hidden>${icon("camera",14)}导入底图</button><input type="file" id="m-file" accept="image/png,image/jpeg,image/webp" hidden>
      <button class="icon-btn" id="m-refresh" aria-label="刷新态势" title="刷新态势">${icon("refresh",16)}</button></div></header>
    <section class="twin-metrics" aria-label="当前空间关键指标" data-twin-metrics></section>
    <div class="twin-scene-toolbar"><div class="twin-scene-identity"><span class="twin-signal-dot"></span><b data-r="title">厂区空间</b><span class="twin-spatial-label">空间示意 · 非 BIM 模型</span></div>
      <div class="head-left"><button class="btn btn-sm" id="m-edit" hidden aria-pressed="false">编辑点位</button><button class="btn btn-sm" id="m-3d" aria-pressed="true">切换二维</button></div></div>
    <div data-r="body" class="twin-board"><div class="twin-stage skeleton-block"></div><aside class="twin-detail skeleton-block"></aside></div>
    <section class="twin-events"><header><span class="twin-eyebrow">最近关联事件</span><span class="muted" data-r="sync" role="status">正在同步运行态…</span></header><div data-twin-events></div></section>
  </div>`;
  const body = $("[data-r='body']",root), picker = $("#m-plan",root), sync = $("[data-r='sync']",root);
  let tab = "plan", plan = null, geoPoints = [], ratio = .5, imageUrl = "", flat = false, editing = false;
  let selected = null, layer = "all", canWrite = false, dead = false, loading = false, uploading = false, timer = null, loadVersion = 0;
  let pending = new Map(), events = [], lastLiveSignature = "", off = () => {};
  const points = () => tab === "geo" ? geoPoints : (plan?.points || []);
  const pointData = () => points().map((p) => {
    const station = p.station_id || "";
    const meta = store.metas.find((m) => m.id === station);
    const online = !!meta && store.conn === "open" && !isOffline(station);
    const risk = pending.get(station || p.name) || pending.get(p.id) || 0;
    return {...p,station,meta,online,risk,status:risk ? `${risk} 条待处理风险` : !meta ? "运行态未知" : !online ? "运行态离线" : meta.source === "sim" ? "仿真在线" : "运行在线"};
  });
  const visiblePoints = (data) => data.filter((p) => layer === "risk" ? p.risk > 0 : layer === "camera" ? p.kind === "camera" : true);
  const scopedEvents = () => {
    const ids = new Set(points().flatMap((p) => [p.station_id,p.name,p.id]).filter(Boolean));
    return events.filter((r) => ids.has(r.station));
  };
  const paintMetrics = () => {
    const data = pointData(), bound = data.filter((p) => p.station), online = data.filter((p) => p.online);
    const sim = online.filter((p) => p.meta.source === "sim").length, risks = scopedEvents().length;
    $("[data-twin-metrics]",root).innerHTML = [
      ["当前空间点位",data.length,"个",`${data.filter((p) => p.kind === "camera").length} 个摄像头点位`,"accent"],
      ["已关联工位",bound.length,"个",`${data.length - bound.length} 个待关联`,"normal"],
      ["运行在线",online.length,"个",sim ? `含 ${sim} 个仿真工位 · 非真机在线数` : `${bound.length - online.length} 个绑定点位未确认在线`,"info"],
      ["待处理风险",risks,"条",`${data.filter((p) => p.risk).length} 个点位需要关注`,risks ? "risk" : "normal"],
    ].map(([label,value,unit,hint,tone],i) => `<div class="twin-metric ${tone}"><span class="twin-metric-index num">0${i+1}</span><div class="twin-metric-label">${label}</div><div class="twin-metric-number num">${value}<span>${unit}</span></div><span class="twin-metric-hint">${hint}</span></div>`).join("");
  };
  const paintEvents = () => {
    const rows = scopedEvents().slice(0,4);
    $("[data-twin-events]",root).innerHTML = rows.length ? `<div class="twin-event-track">${rows.map((r) => `<a href="#/alerts?station=${encodeURIComponent(r.station)}" class="twin-event"><span class="twin-event-time num">${fmtTime(r.created)}</span><span class="twin-event-message" title="${esc(r.message)}">${esc(r.message)}</span><span class="twin-event-station">${esc(r.station)} ${icon("arrow",12)}</span></a>`).join("")}</div>` : '<div class="twin-event-empty">当前空间暂无关联待处理事件。仅统计已定位点位，不推断未接入设备状态。</div>';
  };
  const inventory = (data) => `<div class="twin-inventory"><div class="twin-section-label">点位目录 <span class="num">${data.length}</span></div>${data.map((p) => `<div class="twin-inventory-row"><button class="twin-inventory-button ${selected === p.id ? "on" : ""}" data-select-point="${esc(p.id)}"><i class="twin-inventory-dot ${p.risk ? "risk" : p.online ? "online" : "unknown"}"></i><span>${esc(p.name)}</span><small>${p.risk ? `${p.risk} 条风险` : p.meta ? (p.online ? "在线" : "离线") : "未知"}</small></button>${canWrite && editing ? `<button class="icon-btn" data-del="${esc(p.id)}" aria-label="删除 ${esc(p.name)}">${icon("cross",14)}</button>` : ""}</div>`).join("") || '<p class="muted">尚未建立点位</p>'}</div>`;
  const detailHtml = (p,data) => {
    if (!p) return `<aside class="twin-detail"><div class="twin-section-label">设备上下文</div><div class="twin-detail-empty">${icon("camera",32)}<h3>空间已就绪，等待设备。</h3><p>导入厂区平面图，添加点位并关联真实工位后，这里将显示运行状态与风险。</p></div>${inventory(data)}</aside>`;
    const rows = events.filter((r) => [p.station || p.name,p.id].includes(r.station));
    const hint = p.risk ? "先复核关联告警，再结合实时画面确认现场状态。" : !p.meta ? "该点位尚未关联可读取运行态的工位。请在编辑模式中完成关联。" : p.meta.source === "sim" ? "当前关联仿真源，用于验证规则链路；不代表现场设备状态。" : !p.online ? "工位运行态未保持连接。请检查分析服务与视频源。" : "当前没有关联待处理告警，可打开实况检查现场。";
    return `<aside class="twin-detail"><div class="twin-section-label">当前聚焦 / ${KIND[p.kind] || "设备"}</div><h3 class="twin-device-title" title="${esc(p.name)}">${esc(p.name)}</h3><div class="twin-state ${p.risk ? "risk" : p.online ? "online" : "unknown"}"><i></i>${esc(p.status)}</div>
      <div class="twin-identity-grid"><div><span>关联工位</span><b title="${esc(p.station)}">${esc(p.station || "未关联")}</b></div><div><span>视频来源</span><b>${p.meta ? (p.meta.source === "sim" ? "仿真源" : esc(p.meta.source)) : "未知"}</b></div><div><span>定位坐标</span><b class="num">${p.lat != null ? `${(+p.lat).toFixed(4)} / ${(+p.lng).toFixed(4)}` : `${p.x.toFixed(3)} / ${p.y.toFixed(3)}`}</b></div><div><span>坐标类型</span><b>${p.lat != null ? "纬度 / 经度" : "归一化 X / Y"}</b></div></div>
      <div class="twin-risk-context ${p.risk ? "risk" : ""}"><div class="twin-section-label">${p.risk ? "风险定位" : "建议动作"}<span class="num">${p.risk ? p.risk + " 条" : ""}</span></div><p>${hint}</p>${rows.slice(0,2).map((r) => `<div class="twin-risk-line">${icon("alarm",13)}<span>${esc(r.message)}</span></div>`).join("")}</div>
      <div class="twin-detail-actions">${p.meta ? `<a class="btn btn-primary" href="#/live/${encodeURIComponent(p.station)}">${icon("live",14)}打开实时画面</a>` : ""}<a class="btn" href="#/alerts?station=${encodeURIComponent(p.station || p.name)}">查看关联告警</a></div>${inventory(data)}</aside>`;
  };
  const paintBoard = () => {
    if (dead) return;
    paintMetrics(); paintEvents();
    if (editing) return;
    const data = pointData(), visible = visiblePoints(data);
    if (!visible.some((p) => p.id === selected)) selected = visible.find((p) => p.risk)?.id || visible[0]?.id || null;
    const focus = data.find((p) => p.id === selected);
    const exists = tab === "geo" ? geoPoints.length > 0 : !!plan;
    body.innerHTML = `<section class="twin-stage ${flat ? "flat" : ""}"><div class="twin-stage-top"><div class="twin-stage-coordinate"><span>视图 ${flat ? "02" : "01"}</span><b>${flat ? "二维总览" : "空间总览"}</b></div>
      <div class="twin-layer-switch" role="group" aria-label="设备图层">${[["all","全部点位"],["risk","风险定位"],["camera","摄像头"]].map(([value,label]) => `<button data-layer="${value}" class="${layer === value ? "on" : ""}" aria-pressed="${layer === value}">${label}</button>`).join("")}</div></div>
      ${exists ? twinScene({points:visible,image:imageUrl,ratio,selected,flat}) : '<div class="twin-import-empty"><div class="twin-empty-axis"></div><span class="twin-eyebrow">建立你的厂区空间视图</span><h3>从一张平面图开始。</h3><p>导入 PNG / JPG，关联工位，让运行状态与现场位置对齐。</p><button class="btn" data-start-upload>导入厂区底图</button></div>'}
      ${exists && !visible.length ? `<div class="twin-layer-empty">${data.length ? "该图层暂无匹配点位" : "底图已导入，点击“编辑点位”建立设备位置"}</div>` : ""}
      <div class="twin-stage-legend"><span><i class="online"></i>运行在线</span><span><i class="risk"></i>待处理风险</span><span><i class="unknown"></i>运行态未知</span></div><span class="twin-stage-note">${tab === "geo" ? "坐标分布示意 · 无道路底图" : "基于上传底图的设备定位 · 不推测建筑高度或摄像头视场"}</span></section>${detailHtml(focus,data)}`;
    body.querySelectorAll("[data-layer]").forEach((b) => b.onclick = () => { layer = b.dataset.layer; paintBoard(); });
    body.querySelectorAll("[data-pt],[data-select-point]").forEach((node) => {
      const choose = () => { selected = node.dataset.pt || node.dataset.selectPoint; if (node.dataset.selectPoint && !visible.some((p) => p.id === selected)) layer = "all"; paintBoard(); };
      node.onclick = choose; node.onkeydown = (e) => { if (["Enter"," "].includes(e.key)) { e.preventDefault(); choose(); } };
    });
    const start = $("[data-start-upload]",body); if (start) { start.hidden = !canWrite; start.onclick = () => $("#m-file",root).click(); }
    lastLiveSignature = data.map((p) => `${p.id}:${p.status}`).join("|");
  };
  const renderEditor = () => {
    const data = pointData(), w = 1000, h = w * ratio;
    body.innerHTML = `<section class="twin-stage twin-editor"><div class="twin-editor-head"><b>二维定位编辑</b><span>点击底图选择位置，再保存点位</span></div><div class="twin-editor-map" style="--plan-ratio:${ratio}"><img src="${esc(imageUrl)}" alt="${esc(plan.name)} 平面图"><svg class="plan-svg edit" viewBox="0 0 ${w} ${h}" aria-label="二维点位编辑">${data.map((p) => `<g data-pt="${esc(p.id)}" class="pt"><circle cx="${p.x*w}" cy="${p.y*h}" r="9"/><text x="${p.x*w+14}" y="${p.y*h+4}">${esc(p.name)}</text></g>`).join("")}</svg></div></section>
      <aside class="twin-detail"><form id="m-point-form" class="site-point-form"><div class="twin-section-label">新建空间点位</div><span class="muted" data-coordinates>先点击底图选择位置</span><label class="field"><span>点位名称</span><input name="name" required maxlength="100" placeholder="如：东门摄像头"></label><label class="field"><span>设备类型</span><select name="kind">${Object.entries(KIND).map(([value,label]) => `<option value="${value}">${label}</option>`).join("")}</select></label>
      <label class="field"><span>关联工位</span><select name="station_id"><option value="">不关联 / 运行态未知</option>${store.metas.map((m) => `<option value="${esc(m.id)}">${esc(m.name)} · ${esc(m.id)}</option>`).join("")}</select></label><button class="btn btn-primary" type="submit" disabled>保存点位</button></form>${inventory(data)}</aside>`;
    const svg = $(".plan-svg",body), form = $("#m-point-form",body); let coordinates = null;
    svg.onclick = (e) => {
      const node = e.target.closest("[data-pt]"); if (node) return;
      const point = svg.createSVGPoint(); point.x = e.clientX; point.y = e.clientY;
      const matrix = svg.getScreenCTM(); if (!matrix) return;
      const local = point.matrixTransform(matrix.inverse());
      coordinates = [Math.max(0,Math.min(1,local.x/w)),Math.max(0,Math.min(1,local.y/h))];
      $("[data-coordinates]",form).textContent = `位置 ${coordinates.map((v) => v.toFixed(3)).join(" / ")}`;
      $("button[type=submit]",form).disabled = false;
      svg.querySelector("[data-draft]")?.remove(); const circle = document.createElementNS("http://www.w3.org/2000/svg","circle");
      for (const [key,value] of Object.entries({"data-draft":"",cx:local.x,cy:local.y,r:9,class:"twin-editor-draft"})) circle.setAttribute(key,value);
      svg.appendChild(circle);
    };
    form.onsubmit = async (e) => {
      e.preventDefault(); if (!coordinates) return; const b = $("button[type=submit]",form); b.disabled = true;
      try { await api.sitePoint(plan.id,form.elements.name.value.trim(),form.elements.kind.value,...coordinates,form.elements.station_id.value); await load(); toast("点位已保存"); }
      catch (err) { toast(err.message,"error"); b.disabled = false; }
    };
    body.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => { b.disabled = true; try { await api.sitePointDelete(plan.id,b.dataset.del); await load(); toast("点位已删除"); } catch (e) { toast(e.message,"error"); b.disabled = false; } });
    body.querySelectorAll("[data-select-point]").forEach((b) => b.onclick = () => {
      selected = b.dataset.selectPoint;
      body.querySelectorAll("[data-pt]").forEach((node) => node.classList.toggle("selected",node.dataset.pt === selected));
      body.querySelectorAll("[data-select-point]").forEach((node) => node.classList.toggle("on",node.dataset.selectPoint === selected));
    });
  };
  const load = async () => {
    const version = ++loadVersion;
    try {
      if (tab === "plan") {
        const list = await api.plansList(); if (dead || version !== loadVersion) return;
        const id = list.some((p) => p.id === picker.value) ? picker.value : list[0]?.id;
        picker.innerHTML = list.length ? list.map((p) => `<option value="${esc(p.id)}" ${p.id === id ? "selected" : ""}>${esc(p.name)}</option>`).join("") : '<option value="">暂无底图</option>';
        if (id) {
          const detail = await api.plan(id), image = new Image(); image.src = `/api/plans/${encodeURIComponent(id)}/image`; await image.decode();
          if (dead || version !== loadVersion) return;
          plan = detail; ratio = image.naturalHeight/image.naturalWidth; imageUrl = image.src;
        } else { plan = null; imageUrl = ""; editing = false; }
        $("[data-r='title']",root).textContent = plan?.name || "待建立厂区空间";
      } else {
        const result = await api.geoMap(); if (dead || version !== loadVersion) return;
        const rows = result.points || [], minLat = Math.min(...rows.map((p) => p.lat))-.002, maxLat = Math.max(...rows.map((p) => p.lat))+.002;
        const minLng = Math.min(...rows.map((p) => p.lng))-.002, maxLng = Math.max(...rows.map((p) => p.lng))+.002;
        geoPoints = rows.map((p) => ({...p,kind:KIND[p.kind] ? p.kind : "camera",x:(p.lng-minLng)/(maxLng-minLng),y:1-(p.lat-minLat)/(maxLat-minLat)}));
        ratio = .6; imageUrl = ""; editing = false; $("[data-r='title']",root).textContent = "经纬度点位空间";
      }
      $("#m-edit",root).disabled = !plan || tab !== "plan";
      $("#m-edit",root).textContent = editing ? "完成编辑" : "编辑点位";
      $("#m-edit",root).setAttribute("aria-pressed",editing);
      $("#m-3d",root).disabled = editing;
      $("#m-3d",root).textContent = flat ? "切换空间" : "切换二维";
      $("#m-3d",root).setAttribute("aria-pressed",!flat);
      if (editing) renderEditor(); else paintBoard();
      paintMetrics(); paintEvents();
    } catch (e) { if (!dead && version === loadVersion) { body.innerHTML = `<section class="twin-stage">${errorBlock(e.message)}</section>`; $("[data-retry]",body).onclick = load; } }
  };
  const refreshAlerts = async () => {
    try {
      const rows = await api.alerts({status:"pending",limit:500}); if (dead) return;
      events = rows; pending = new Map(); rows.forEach((r) => pending.set(r.station,(pending.get(r.station)||0)+1));
      if (!uploading) paintBoard(); sync.textContent = "已同步待处理告警 · 每 5 秒更新";
    } catch (e) { if (!dead) sync.textContent = `同步失败：${e.message}`; }
  };
  root.querySelector(".seg-ctl").onclick = (e) => {
    const b = e.target.closest("[data-tab]"); if (!b) return; tab = b.dataset.tab; editing = false; selected = null;
    root.querySelectorAll("[data-tab]").forEach((n) => { n.classList.toggle("on",n===b); n.setAttribute("aria-selected",n===b); });
    root.querySelectorAll("[data-plan-only]").forEach((n) => n.hidden = tab !== "plan" || (n.id === "m-upload" && !canWrite));
    $("#m-edit",root).hidden = tab !== "plan" || !canWrite; load();
  };
  picker.onchange = () => { selected = null; load(); };
  $("#m-edit",root).onclick = () => { editing = !editing; flat = editing; $("#m-3d",root).disabled = editing; $("#m-3d",root).title = editing ? "完成编辑后可切换空间视图" : ""; load(); };
  $("#m-3d",root).onclick = () => { if (editing) return; flat = !flat; $("#m-3d",root).textContent = flat ? "切换空间" : "切换二维"; $("#m-3d",root).setAttribute("aria-pressed",!flat); paintBoard(); };
  $("#m-refresh",root).onclick = () => Promise.all([load(),refreshAlerts()]);
  $("#m-upload",root).onclick = () => $("#m-file",root).click();
  $("#m-file",root).onchange = async (e) => {
    const file = e.target.files[0]; if (!file) return; const b = $("#m-upload",root), previous = picker.value;
    uploading = true; b.disabled = true; picker.disabled = true; $("#m-edit",root).disabled = true; body.innerHTML = '<div class="twin-stage skeleton-block" aria-label="正在上传底图"></div>';
    try { await api.planUpload(file.name.replace(/\.[^.]+$/,""),file); uploading = false; picker.value = ""; await load(); toast("底图已导入"); }
    catch (e) { uploading = false; toast(e.message,"error"); picker.value = previous; await load(); }
    finally { uploading = false; b.disabled = false; picker.disabled = false; e.target.value = ""; }
  };
  const init = async () => {
    try {
      const me = await api.me(); if (dead) return; canWrite = ["admin","operator"].includes(me.role);
      $("#m-upload",root).hidden = !canWrite; $("#m-edit",root).hidden = !canWrite;
      await load(); await refreshAlerts();
      off = subscribe((type) => { if (!dead && !editing && ["state","conn"].includes(type)) { const signature = pointData().map((p) => `${p.id}:${p.status}`).join("|"); if (signature !== lastLiveSignature) paintBoard(); } });
      if (!dead) timer = setInterval(() => { if (!loading) { loading = true; refreshAlerts().finally(() => loading = false); } },5000);
    } catch (e) { if (!dead) { body.innerHTML = errorBlock(e.message); $("[data-retry]",body).onclick = init; } }
  };
  paintMetrics(); init();
  return () => { dead = true; loadVersion++; clearInterval(timer); off(); };
}
