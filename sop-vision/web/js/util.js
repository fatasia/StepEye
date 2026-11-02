export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC_MAP[c]);

/** 内容不变时不写 DOM，避免高频推送造成无意义重排。 */
export function setHTML(el, html) {
  if (el.__html !== html) {
    el.innerHTML = html;
    el.__html = html;
  }
}

export function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}

const pad = (n) => String(n).padStart(2, "0");

export function fmtTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function fmtClock(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function relTime(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 10) return "刚刚";
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return fmtTime(ms);
}

/** 时长：<60s 显示 12.3 s；≥60s 显示 1:05 min。返回带单位的 HTML。 */
export function durHtml(s) {
  if (s == null || Number.isNaN(s)) return "—";
  if (s >= 60) return `${Math.floor(s / 60)}:${pad(Math.round(s % 60))}<span class="unit">min</span>`;
  return `${s.toFixed(1)}<span class="unit">s</span>`;
}

export const pct = (v, d = 1) => (v == null ? "—" : (v * 100).toFixed(d));
export const int = (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN"));

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

export function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}
