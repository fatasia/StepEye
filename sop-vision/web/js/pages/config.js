import { api } from "../api.js";
import { icon } from "../icons.js";
import { store, subscribe } from "../store.js";
import { emptyBlock, errorBlock, toast } from "../ui.js";
import { $, copyText, esc, setHTML } from "../util.js";

const TABS = [["steps", "步骤规则"], ["roi", "结果核验"], ["yaml", "规则源码"]];
const MODE = { diff_empty: "空台差分", template: "模板匹配" };
const EVIDENCE_MODE = { all: "全部满足", any: "任一满足", seq: "依次满足" };

function mapHtml(cfg, selected) {
  const { width: w, height: h } = cfg.meta;
  const polys = cfg.zones.map((z) => {
    const pts = z.polygon.map(([x, y]) => `${(x * w).toFixed(1)},${(y * h).toFixed(1)}`).join(" ");
    const cx = (z.polygon.reduce((a, p) => a + p[0], 0) / z.polygon.length) * w;
    const top = Math.min(...z.polygon.map((p) => p[1])) * h;
    return `<g class="zone ${z.id === selected ? "sel" : ""}" data-zone="${esc(z.id)}" tabindex="0" role="button" aria-label="区域 ${esc(z.id)}"><polygon points="${pts}"/><text x="${cx.toFixed(1)}" y="${(top - 5).toFixed(1)}" text-anchor="middle">${esc(z.id)}</text></g>`;
  }).join("");
  return `<svg class="zone-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet">${polys}</svg>`;
}

function stepsHtml(cfg, selected) {
  const c = cfg.cycle;
  const head = `<div class="kv-grid cycle-def">
    <div class="kv"><span class="muted">周期开始</span><span>${esc(c.start)}</span></div>
    <div class="kv"><span class="muted">周期结束</span><span>${esc(c.end)}</span></div>
    <div class="kv"><span class="muted">节拍目标</span><span class="num">${c.takt_s ?? "—"}${c.takt_s ? '<span class="unit">s</span>' : ""}</span></div>
    <div class="kv"><span class="muted">错序检查宽限</span><span class="num">${c.grace_ms}<span class="unit">ms</span></span></div></div>`;
  const items = cfg.steps.map((s, i) => {
    const ref = selected && s.conds.some((t) => t.includes(`「${selected}」`));
    return `<li class="cfg-step ${ref ? "ref" : ""}">
      <span class="cfg-idx num">${i + 1}</span>
      <div class="cfg-main">
        <div class="cfg-name"><span class="ellipsis">${esc(s.name)}</span><span class="mono faint">${esc(s.id)}</span>${s.optional ? '<span class="tag tone-muted">可选</span>' : ""}${s.timeout_s ? `<span class="tag tone-warning">超时 ${s.timeout_s} s</span>` : ""}</div>
        <div class="cfg-cond muted"><span class="tag tone-muted">${EVIDENCE_MODE[s.mode]}</span>${s.conds.map((t) => `<span>${esc(t)}</span>`).join("")}</div>
        <div class="cfg-pre faint">前驱：${s.preds.length ? s.preds.map(esc).join("、") : "无（周期开始即激活）"}</div>
      </div></li>`;
  }).join("");
  return `${head}<ol class="cfg-steps">${items}</ol>`;
}

function roiHtml(cfg, id, live) {
  if (!cfg.roi_states.length) return emptyBlock("未配置结果核验", "在 roi_states 中为区域添加核验方式后，这里会显示状态与标定情况。");
  const sim = cfg.meta.source === "sim";
  const rows = cfg.roi_states.map((r) => {
    const l = live?.[r.zone];
    const btn = `<button class="btn btn-sm" data-calib="${esc(r.zone)}" ${sim ? 'disabled title="仿真工位使用内置参考图，无需标定"' : ""}>${icon("camera", 14)}采集参考图</button>`;
    return `<tr><td class="mono">${esc(r.zone)}</td><td>${MODE[r.mode]}</td>
      <td><span class="tag tone-success">${esc(r.positive_state)}</span> <span class="faint">/</span> <span class="tag tone-muted">${esc(r.negative_state)}</span></td>
      <td>${l?.state ? esc(l.state) : '<span class="faint">—</span>'}</td><td class="num right">${l ? l.score.toFixed(2) : "—"}</td>
      <td>${l?.calibrated ? `<span class="tone-success-text">${icon("check", 14)} 已标定</span>` : `<span class="tone-warning-text">${icon("warn", 14)} 未标定</span>`}</td><td class="right">${btn}</td></tr>`;
  }).join("");
  return `<div class="table-wrap"><table class="table"><thead><tr><th>区域</th><th>方式</th><th>状态（成立 / 不成立）</th><th>当前</th><th class="right">得分</th><th>参考图</th><th class="right">操作</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="hint muted">标定前，请先把对应区域摆到参考状态：空台差分需要<b>区域为空</b>；模板匹配需要<b>装配正确</b>。</p>`;
}

export function render(root, params) {
  const id = params.id || store.metas[0]?.id;
  if (!id) {
    root.innerHTML = emptyBlock("没有可配置的工位", "先用 python -m visual serve 配置文件.yaml 启动工位。");
    return () => {};
  }
  let off = () => {}, dead = false;
  const load = async () => {
    let cfg;
    try { cfg = await api.config(id); } catch (e) {
      root.innerHTML = errorBlock(e.message);
      root.querySelector("[data-retry]").onclick = load;
      return;
    }
    if (dead) return;
    let tab = "steps", selected = "";
    root.innerHTML = `
      <div class="cfg">
        <section class="panel cfg-map">
          <header class="panel-head"><div class="head-left"><h2>现场画面与区域</h2>
            <label class="select-wrap"><span class="sr-only">切换工位</span><select id="sel">${store.metas.map((m) => `<option value="${esc(m.id)}" ${m.id === id ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></label></div>
            <button class="btn" disabled title="区域编辑将在下一版本提供；当前请在规则源码中修改 zones">${icon("lock", 14)}编辑区域</button></header>
          <div class="map" style="aspect-ratio:${cfg.meta.width}/${cfg.meta.height}"><img src="${api.snapshot(id, true)}" alt="现场画面"><div data-r="map"></div></div>
          <p class="hint muted">点击区域可高亮引用它的步骤。共 ${cfg.zones.length} 个区域，坐标为归一化比例，与分辨率无关。</p>
        </section>
        <section class="panel cfg-detail">
          <header class="panel-head"><div class="tabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" class="${k === tab ? "on" : ""}" aria-selected="${k === tab}">${t}</button>`).join("")}</div>
            <span class="mono faint" title="规则指纹">${esc(cfg.meta.rules_hash)}</span></header>
          <div class="panel-body" data-r="pane"></div>
        </section>
      </div>`;
    const pane = $('[data-r="pane"]', root), map = $('[data-r="map"]', root);
    const paint = () => {
      setHTML(map, mapHtml(cfg, selected));
      root.querySelectorAll("[data-tab]").forEach((b) => { b.classList.toggle("on", b.dataset.tab === tab); b.setAttribute("aria-selected", b.dataset.tab === tab); });
      if (tab === "steps") setHTML(pane, stepsHtml(cfg, selected));
      else if (tab === "roi") setHTML(pane, roiHtml(cfg, id, store.states.get(id)?.roi));
      else setHTML(pane, `<div class="code-head"><span class="muted">只读 · 修改请编辑 ${esc(cfg.meta.id)} 对应的 YAML 文件</span><button class="btn btn-sm" data-copy>${icon("copy", 14)}复制</button></div><pre class="code">${esc(cfg.yaml)}</pre>`);
    };
    $("#sel", root).onchange = (e) => (location.hash = `#/config/${encodeURIComponent(e.target.value)}`);
    root.addEventListener("click", async (e) => {
      const t = e.target.closest("[data-tab]"); if (t) { tab = t.dataset.tab; return paint(); }
      const z = e.target.closest("[data-zone]"); if (z) { selected = selected === z.dataset.zone ? "" : z.dataset.zone; return paint(); }
      if (e.target.closest("[data-copy]")) return toast((await copyText(cfg.yaml)) ? "规则源码已复制" : "复制失败，请手动选择文本复制");
      const c = e.target.closest("[data-calib]");
      if (c && !c.disabled) {
        c.disabled = true;
        try { await api.calibrate(id, [c.dataset.calib]); toast(`已提交标定：${c.dataset.calib}`); } catch (err) { toast(err.message, "error"); }
        c.disabled = false;
      }
    });
    root.addEventListener("keydown", (e) => { const z = e.target.closest?.("[data-zone]"); if (z && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); z.dispatchEvent(new Event("click", { bubbles: true })); } });
    paint();
    off = subscribe((t, p) => { if (t === "state" && p === id && tab === "roi") paint(); });
  };
  root.innerHTML = `<div class="cfg"><section class="panel skeleton-block" style="min-height:420px"></section><section class="panel skeleton-block" style="min-height:420px"></section></div>`;
  load();
  return () => { dead = true; off(); };
}
