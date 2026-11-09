import { icon } from "./icons.js";
import { esc } from "./util.js";

/* ---------- Esc 栈：最上层的浮层先关闭 ---------- */
const escStack = [];
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && escStack.length) {
    e.preventDefault();
    escStack[escStack.length - 1]();
  }
});
export function onEsc(fn) {
  escStack.push(fn);
  return () => {
    const i = escStack.indexOf(fn);
    if (i >= 0) escStack.splice(i, 1);
  };
}

/* ---------- Toast：成功 3s，失败 6s，均可手动关闭 ---------- */
export function toast(message, kind = "success") {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  const ic = kind === "error" ? "alarm" : kind === "info" ? "clock" : "check";
  el.innerHTML = `${icon(ic, 16)}<span class="toast-msg">${esc(message)}</span><button class="icon-btn" aria-label="关闭提示">${icon("cross", 14)}</button>`;
  const close = () => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 220);
  };
  el.querySelector("button").onclick = close;
  host.appendChild(el);
  setTimeout(close, kind === "error" ? 6000 : 3000);
}

/* ---------- 抽屉 ---------- */
export function openDrawer({ title, subtitle = "", html = "", onClose }) {
  const layer = document.getElementById("layer");
  const prevFocus = document.activeElement;
  const wrap = document.createElement("div");
  wrap.className = "drawer-wrap";
  wrap.innerHTML = `
    <div class="scrim" data-close></div>
    <aside class="drawer" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <header class="drawer-head">
        <div class="ellipsis"><h2 class="drawer-title">${esc(title)}</h2><div class="drawer-sub muted ellipsis">${subtitle}</div></div>
        <button class="icon-btn" data-close aria-label="关闭">${icon("cross", 16)}</button>
      </header>
      <div class="drawer-body">${html}</div>
    </aside>`;
  layer.appendChild(wrap);
  requestAnimationFrame(() => wrap.classList.add("in"));
  let closed = false;
  const off = onEsc(close);
  function close() {
    if (closed) return;
    closed = true;
    off();
    wrap.classList.remove("in");
    setTimeout(() => wrap.remove(), 380);
    prevFocus?.focus?.();
    onClose?.();
  }
  wrap.querySelectorAll("[data-close]").forEach((b) => (b.onclick = close));
  wrap.querySelector(".drawer-head .icon-btn").focus();
  return { el: wrap, body: wrap.querySelector(".drawer-body"), close };
}

/* ---------- 图片放大 ---------- */
export function openLightbox(src, caption = "") {
  const layer = document.getElementById("layer");
  const wrap = document.createElement("div");
  wrap.className = "lightbox in";
  wrap.innerHTML = `<div class="scrim" data-close></div>
    <figure class="lightbox-fig"><img src="${esc(src)}" alt="${esc(caption)}"><figcaption>${esc(caption)}</figcaption></figure>
    <button class="icon-btn lightbox-x" data-close aria-label="关闭大图">${icon("cross", 18)}</button>`;
  layer.appendChild(wrap);
  const off = onEsc(close);
  function close() {
    off();
    wrap.remove();
  }
  wrap.querySelectorAll("[data-close]").forEach((b) => (b.onclick = close));
}

/* ---------- 状态词汇（全系统唯一：颜色 + 图标 + 文字） ---------- */
export const STATION_STATUS = {
  running: { text: "作业中", icon: "running", tone: "success" },
  idle: { text: "空闲", icon: "idle", tone: "muted" },
  warn: { text: "预警", icon: "warn", tone: "warning" },
  alarm: { text: "告警", icon: "alarm", tone: "danger" },
  offline: { text: "离线", icon: "offline", tone: "info" },
};
export const RESULT = {
  PASS: { text: "合格", icon: "check", tone: "success" },
  FAIL: { text: "不合格", icon: "cross", tone: "danger" },
  UNCERTAIN: { text: "待复核", icon: "question", tone: "warning" },
};
export const STEP_STATUS = {
  pending: { text: "待执行", icon: "idle", tone: "muted" },
  active: { text: "进行中", icon: "active", tone: "accent" },
  done: { text: "已完成", icon: "check", tone: "success" },
  early: { text: "错序完成", icon: "order", tone: "warning" },
};
export const VIOLATION = {
  missing_step: { text: "漏步", tone: "danger" },
  wrong_order: { text: "错序", tone: "danger" },
  step_timeout: { text: "步骤超时", tone: "warning" },
  cycle_overtime: { text: "周期超节拍", tone: "warning" },
};

export const badge = (def, extra = "") =>
  `<span class="badge tone-${def.tone} ${extra}">${icon(def.icon, 13)}<span>${def.text}</span></span>`;
export const stationBadge = (key) => badge(STATION_STATUS[key] ?? STATION_STATUS.offline, key === "alarm" ? "pulse" : "");
export const resultBadge = (r) => badge(RESULT[r] ?? RESULT.UNCERTAIN);
export const violationTag = (kind) => {
  const v = VIOLATION[kind] ?? { text: kind, tone: "muted" };
  return `<span class="tag tone-${v.tone}">${v.text}</span>`;
};

/* ---------- 通用片段 ---------- */
export const errorBlock = (message, retryAttr = "data-retry") =>
  `<div class="state-block error" role="alert">${icon("alarm", 28)}<div class="state-title">加载失败</div><p>${esc(message)}</p><button class="btn" ${retryAttr}>${icon("refresh", 14)}重试</button></div>`;
export const emptyBlock = (title, hint, action = "") =>
  `<div class="state-block">${icon("inbox", 28)}<div class="state-title">${esc(title)}</div><p>${esc(hint)}</p>${action}</div>`;

/* ---------- 文件选择控件 ----------
   浏览器原生 input[type=file] 会显示英文 "Choose File / No file chosen",
   与中文界面不一致。保留原 input(表单语义/name 不变),在它外面加一层
   统一的"选择文件 + 文件名"外观。 */
let fileInputObserver = null;

export function enhanceFileInputs(root = document) {
  root.querySelectorAll('input[type="file"]:not([data-file-ui])').forEach((input) => {
    if (input.hidden || input.closest(".file-picker")) return;
    input.dataset.fileUi = "1";
    const wrap = document.createElement("span");
    wrap.className = "file-picker";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const button = document.createElement("span");
    button.className = "file-picker-btn";
    button.textContent = "选择文件";
    const name = document.createElement("span");
    name.className = "file-picker-name";
    wrap.append(button, name);
    input.classList.add("file-picker-input");
    const sync = () => {
      const files = input.files ? Array.from(input.files) : [];
      name.textContent = files.length ? files.map((f) => f.name).join("、") : "未选择文件";
      name.title = name.textContent;
      wrap.classList.toggle("has-file", files.length > 0);
    };
    input.addEventListener("change", sync);
    // input 在 <label> 内时,点击 label 已能唤起文件框,再绑 click 会弹两次。
    if (!input.closest("label")) {
      wrap.addEventListener("click", (event) => {
        if (event.target !== input) input.click();
      });
    }
    sync();
  });
}

export function watchFileInputs() {
  if (fileInputObserver) return;
  let queued = false;
  const run = () => {
    queued = false;
    enhanceFileInputs(document);
  };
  fileInputObserver = new MutationObserver(() => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(run);
    }
  });
  fileInputObserver.observe(document.body, { childList: true, subtree: true });
  run();
}
