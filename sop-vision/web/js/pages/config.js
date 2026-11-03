import { openReferenceGallery } from "./action-references.js";
import { openSafetySettings } from "./safety-config.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { store, subscribe } from "../store.js";
import { emptyBlock, errorBlock, toast } from "../ui.js";
import { $, copyText, esc, setHTML } from "../util.js";

const TABS = [["steps", "步骤规则"], ["roi", "结果核验"], ["yaml", "规则源码"]];
const MODE = { diff_empty: "空台差分", template: "模板匹配" };
const EVIDENCE_MODE = { all: "全部满足", any: "任一满足", seq: "依次满足" };
const MIN_ZONE = 0.02; // 归一化最小边长,防止误生成看不见的小框

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

/* ---------- 区域编辑模式:只做矩形(拖拽画框 / 整体移动 / 角把手缩放),坐标归一化 ---------- */
const rectOf = (z) => {
  const xs = z.polygon.map((p) => p[0]), ys = z.polygon.map((p) => p[1]);
  return { id: z.id, x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
};
const clamp01 = (v) => Math.min(1, Math.max(0, v));

function editSvgHtml(cfg, zones, sel) {
  const { width: w, height: h } = cfg.meta;
  const rects = zones.map((z, i) => {
    const x = z.x0 * w, y = z.y0 * h, rw = Math.max(2, (z.x1 - z.x0) * w), rh = Math.max(2, (z.y1 - z.y0) * h);
    return `<g class="ez ${i === sel ? "sel" : ""}" data-i="${i}"><rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${rw.toFixed(1)}" height="${rh.toFixed(1)}"/><text x="${(x + 4).toFixed(1)}" y="${Math.max(12, y - 4).toFixed(1)}">${esc(z.id)}</text><circle class="ez-handle" data-handle="${i}" cx="${(x + rw).toFixed(1)}" cy="${(y + rh).toFixed(1)}" r="6"/></g>`;
  }).join("");
  return `<svg class="zone-svg edit" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet">${rects}</svg>`;
}

function editorHtml(zones, sel) {
  const rows = zones.map((z, i) => `<div class="ez-row ${i === sel ? "sel" : ""}" data-i="${i}">
      <input value="${esc(z.id)}" data-rename="${i}" aria-label="区域名" maxlength="40">
      <span class="mono faint nowrap">${((z.x1 - z.x0) * 100).toFixed(0)}×${((z.y1 - z.y0) * 100).toFixed(0)}%</span>
      <button class="icon-btn" data-del="${i}" aria-label="删除区域 ${esc(z.id)}">${icon("cross", 14)}</button></div>`).join("");
  return `<div class="ez-panel"><div class="ez-list">${rows || '<div class="inline-empty muted">还没有区域:在画面上拖一个框开始</div>'}</div>
    <div class="ez-actions"><button class="btn btn-primary" data-save>${icon("check", 14)}保存并生效</button><button class="btn" data-cancel>取消</button></div>
    <p class="hint muted">在画面上<b>拖拽画新区域</b>,拖动框整体移动,右下角把手调整大小,Delete 删除选中。保存会重写 YAML(注释会丢失)并热重载工位。</p></div>`;
}

function stepsHtml(cfg, selected) {
  const c = cfg.cycle;
  const head = c ? `<div class="kv-grid cycle-def">
    <div class="kv"><span class="muted">周期开始</span><span>${esc(c.start)}</span></div>
    <div class="kv"><span class="muted">周期结束</span><span>${esc(c.end)}</span></div>
    <div class="kv"><span class="muted">节拍目标</span><span class="num">${c.takt_s ?? "—"}${c.takt_s ? '<span class="unit">s</span>' : ""}</span></div>
    <div class="kv"><span class="muted">错序检查宽限</span><span class="num">${c.grace_ms}<span class="unit">ms</span></span></div></div>`
    : `<div class="inline-empty muted">纯安全岗:没有生产周期,只有常驻安全规则</div>`;
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
  let safetyDrawer = null;
  let off = () => {}, dead = false;
  const load = async () => {
    let cfg;
    try { cfg = await api.config(id); } catch (e) {
      root.innerHTML = errorBlock(e.message);
      root.querySelector("[data-retry]").onclick = load;
      return;
    }
    if (dead) return;
    let tab = "steps", selected = "", editing = false, ez = [], ezSel = -1, drag = null;

    const bindEdit = (svg) => {
      const pt = (ev) => {
        const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(svg.getScreenCTM().inverse());
        return [clamp01(p.x / cfg.meta.width), clamp01(p.y / cfg.meta.height)];
      };
      svg.addEventListener("pointerdown", (e) => {
        const [x, y] = pt(e);
        const handle = e.target.closest("[data-handle]");
        if (handle) {
          drag = { type: "resize", i: +handle.dataset.handle };
          svg.setPointerCapture(e.pointerId);
        } else {
          const g = e.target.closest("[data-i]");
          if (g) {
            ezSel = +g.dataset.i;
            drag = { type: "move", i: ezSel, sx: x, sy: y, orig: { ...ez[ezSel] } };
          } else {
            let nid = `zone${ez.length + 1}`;
            while (ez.some((z) => z.id === nid)) nid += "_1";
            ez.push({ id: nid, x0: x, y0: y, x1: x, y1: y });
            ezSel = ez.length - 1;
            drag = { type: "draw", i: ezSel };
          }
          svg.setPointerCapture(e.pointerId);
        }
        paint();
      });
      svg.addEventListener("pointermove", (e) => {
        if (!drag) return;
        const [x, y] = pt(e);
        const z = ez[drag.i];
        if (drag.type === "move") {
          const dx = x - drag.sx, dy = y - drag.sy;
          z.x0 = clamp01(drag.orig.x0 + dx); z.x1 = clamp01(drag.orig.x1 + dx);
          z.y0 = clamp01(drag.orig.y0 + dy); z.y1 = clamp01(drag.orig.y1 + dy);
        } else { // draw / resize
          z.x1 = Math.max(z.x0 + MIN_ZONE, x); z.y1 = Math.max(z.y0 + MIN_ZONE, y);
        }
        const { width: w, height: h } = cfg.meta;
        const g = svg.querySelector(`[data-i="${drag.i}"]`);
        if (g) {
          const rx = z.x0 * w, ry = z.y0 * h;
          g.querySelector("rect").setAttribute("x", rx.toFixed(1));
          g.querySelector("rect").setAttribute("y", ry.toFixed(1));
          g.querySelector("rect").setAttribute("width", ((z.x1 - z.x0) * w).toFixed(1));
          g.querySelector("rect").setAttribute("height", ((z.y1 - z.y0) * h).toFixed(1));
          const t = g.querySelector("text"), c = g.querySelector(".ez-handle");
          t.setAttribute("x", (rx + 4).toFixed(1)); t.setAttribute("y", Math.max(12, ry - 4).toFixed(1));
          c.setAttribute("cx", (z.x1 * w).toFixed(1)); c.setAttribute("cy", (z.y1 * h).toFixed(1));
        }
      });
      const up = () => {
        if (!drag) return;
        if (drag.type === "draw") {
          const z = ez[drag.i];
          if (z.x1 - z.x0 < MIN_ZONE || z.y1 - z.y0 < MIN_ZONE) { ez.splice(drag.i, 1); ezSel = -1; }
        }
        drag = null;
        paint();
      };
      svg.addEventListener("pointerup", up);
      svg.addEventListener("pointercancel", up);
    };

    const saveZones = async () => {
      const ids = ez.map((z) => z.id.trim());
      if (ids.some((v) => !v)) return toast("区域名不能为空", "error");
      if (new Set(ids).size !== ids.length) return toast("区域名重复,请改名字", "error");
      try {
        await api.saveZones(id, ez.map((z) => ({ id: z.id.trim(), polygon: [[z.x0, z.y0], [z.x1, z.y0], [z.x1, z.y1], [z.x0, z.y1]] })));
        toast("已保存,工位热重载中", "success");
        editing = false;
        load();
      } catch (e) {
        toast(e.message, "error");
      }
    };

    root.innerHTML = `
      <div class="cfg">
        <section class="panel cfg-map">
          <header class="panel-head"><div class="head-left"><h2>现场画面与区域</h2>
            <label class="select-wrap"><span class="sr-only">切换工位</span><select id="sel">${store.metas.map((m) => `<option value="${esc(m.id)}" ${m.id === id ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></label></div>
            <button class="btn" data-action-references>动作卡</button><button class="btn" data-safety-settings>安全防护</button><button class="btn" data-edit>${editing ? `${icon("cross", 14)}退出编辑` : `${icon("order", 14)}编辑区域`}</button></header>
          <div class="map" style="aspect-ratio:${cfg.meta.width}/${cfg.meta.height}"><img src="${api.snapshot(id, true)}" alt="现场画面"><div data-r="map"></div></div>
          <div data-r="editor"></div>
          <p class="hint muted" data-r="hint"></p>
        </section>
        <section class="panel cfg-detail">
          <header class="panel-head"><div class="tabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" class="${k === tab ? "on" : ""}" aria-selected="${k === tab}">${t}</button>`).join("")}</div>
            <span class="mono faint" title="规则指纹">${esc(cfg.meta.rules_hash)}</span></header>
          <div class="panel-body" data-r="pane"></div>
        </section>
      </div>`;
    const pane = $('[data-r="pane"]', root), map = $('[data-r="map"]', root),
      editor = $('[data-r="editor"]', root), hint = $('[data-r="hint"]', root);
    const paint = () => {
      if (editing) {
        setHTML(map, editSvgHtml(cfg, ez, ezSel));
        bindEdit(map.querySelector("svg"));
        setHTML(editor, editorHtml(ez, ezSel));
        hint.innerHTML = `编辑中:共 ${ez.length} 个区域。`;
      } else {
        setHTML(map, mapHtml(cfg, selected));
        setHTML(editor, "");
        hint.textContent = `点击区域可高亮引用它的步骤。共 ${cfg.zones.length} 个区域，坐标为归一化比例，与分辨率无关。`;
      }
      $('[data-edit]', root).innerHTML = editing ? `${icon("cross", 14)}退出编辑` : `${icon("order", 14)}编辑区域`;
      root.querySelectorAll("[data-tab]").forEach((b) => { b.classList.toggle("on", b.dataset.tab === tab); b.setAttribute("aria-selected", b.dataset.tab === tab); });
      if (tab === "steps") setHTML(pane, stepsHtml(cfg, selected));
      else if (tab === "roi") setHTML(pane, roiHtml(cfg, id, store.states.get(id)?.roi));
      else setHTML(pane, `<div class="code-head"><span class="muted">只读 · 修改请编辑 ${esc(cfg.meta.id)} 对应的 YAML 文件</span><button class="btn btn-sm" data-copy>${icon("copy", 14)}复制</button></div><pre class="code">${esc(cfg.yaml)}</pre>`);
    };
    $("#sel", root).onchange = (e) => (location.hash = `#/config/${encodeURIComponent(e.target.value)}`);
    root.addEventListener("click", async (e) => {
      if (e.target.closest("[data-action-references]")) {
        safetyDrawer?.close();
        safetyDrawer = openReferenceGallery(id);
        return;
      }
      if (e.target.closest("[data-safety-settings]")) {
        safetyDrawer?.close();
        safetyDrawer = openSafetySettings(id);
        return;
      }
      if (e.target.closest("[data-edit]")) {
        editing = !editing;
        if (editing) { ez = cfg.zones.map(rectOf); ezSel = ez.length ? 0 : -1; }
        return paint();
      }
      const t = e.target.closest("[data-tab]"); if (t) { tab = t.dataset.tab; return paint(); }
      if (e.target.closest("[data-save]")) return saveZones();
      if (e.target.closest("[data-cancel]")) { editing = false; return paint(); }
      const del = e.target.closest("[data-del]");
      if (del) { ez.splice(+del.dataset.del, 1); ezSel = Math.min(ezSel, ez.length - 1); return paint(); }
      const row = e.target.closest(".ez-row[data-i]");
      if (row && !e.target.closest("input,button")) { ezSel = +row.dataset.i; return paint(); }
      const z = e.target.closest("[data-zone]"); if (z) { selected = selected === z.dataset.zone ? "" : z.dataset.zone; return paint(); }
      if (e.target.closest("[data-copy]")) return toast((await copyText(cfg.yaml)) ? "规则源码已复制" : "复制失败，请手动选择文本复制");
      const c = e.target.closest("[data-calib]");
      if (c && !c.disabled) {
        c.disabled = true;
        try { await api.calibrate(id, [c.dataset.calib]); toast(`已提交标定：${c.dataset.calib}`); } catch (err) { toast(err.message, "error"); }
        c.disabled = false;
      }
    });
    root.addEventListener("change", (e) => {
      const inp = e.target.closest("[data-rename]");
      if (!inp) return;
      const v = inp.value.trim();
      if (!v || ez.some((z, i) => i !== +inp.dataset.rename && z.id === v)) {
        toast(v ? "区域名重复,已恢复" : "区域名不能为空,已恢复", "error");
        inp.value = ez[+inp.dataset.rename].id;
        return;
      }
      ez[+inp.dataset.rename].id = v;
      paint();
    });
    const onKey = (e) => {
      if (!editing || e.key !== "Delete" || /INPUT|TEXTAREA/.test(document.activeElement.tagName)) return;
      if (ezSel >= 0) { ez.splice(ezSel, 1); ezSel = Math.min(ezSel, ez.length - 1); paint(); }
    };
    document.addEventListener("keydown", onKey);
    root.addEventListener("keydown", (e) => { const z = e.target.closest?.("[data-zone]"); if (z && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); z.dispatchEvent(new Event("click", { bubbles: true })); } });
    paint();
    const unsubState = subscribe((t, p) => { if (t === "state" && p === id && tab === "roi" && !editing) paint(); });
    off = () => { unsubState(); document.removeEventListener("keydown", onKey); };
  };
  root.innerHTML = `<div class="cfg"><section class="panel skeleton-block" style="min-height:420px"></section><section class="panel skeleton-block" style="min-height:420px"></section></div>`;
  load();
  return () => { dead = true; safetyDrawer?.close(); off(); };
}
