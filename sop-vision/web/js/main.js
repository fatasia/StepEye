import { mountAssistant } from "./global-assistant.js";
import * as documents from "./pages/documents.js";
import { icon, LOGO } from "./icons.js";
import { api, ApiError } from "./api.js";
import { bootstrap, connect, store, subscribe } from "./store.js";
import { errorBlock, onEsc } from "./ui.js";
import { $, fmtClock } from "./util.js";
import * as overview from "./pages/overview.js";
import * as live from "./pages/live.js";
import * as trace from "./pages/trace.js";
import * as config from "./pages/config.js";
import * as inspection from "./pages/inspection.js";
import * as alerts from "./pages/alerts.js";
import * as timeline from "./pages/timeline.js";
import * as sitemap from "./pages/sitemap.js";
import * as samples from "./pages/samples.js";
import * as login from "./pages/login.js";
import * as patrol from "./pages/patrol.js";
import * as recordings from "./pages/recordings.js";
import * as labeling from "./pages/labeling.js";
import * as models from "./pages/models.js";
import * as settings from "./pages/settings.js";

const ROUTES = { documents, overview, live, trace, config, inspection, alerts, samples, timeline, sitemap, patrol, recordings, labeling, models, settings };
const TITLES = { documents: "知识与文档", overview: "总览", live: "实时监控", trace: "追溯", config: "SOP 配置", inspection: "拍照即检", alerts: "告警中心", samples: "样本库", timeline: "时间轴标注", sitemap: "厂区图", patrol: "巡更到位", recordings: "录像回放", labeling: "标注工作台", models: "视觉应用", settings: "设置" };
const NAV = [["overview", "总览", "overview"], ["live", "实时监控", "live"], ["trace", "追溯", "trace"], ["inspection", "拍照即检", "camera"], ["alerts", "告警中心", "inbox"], ["samples", "样本库", "copy"], ["models", "视觉应用", "config"], ["timeline", "时间轴标注", "clock"], ["sitemap", "厂区图", "overview"], ["patrol", "巡更到位", "clock"], ["recordings", "录像回放", "live"], ["config", "SOP 配置", "config"], ["documents", "知识与文档", "copy"], ["settings", "设置", "config"]];
const ACCENTS = [["#3ec6c1", "青"], ["#65aee8", "蓝"], ["#59c58d", "绿"], ["#d8ac52", "琥珀"], ["#b08cf0", "紫"]];
const CONN = { open: ["实时连接", "success"], retry: ["连接中断，重连中…", "warning"], connecting: ["正在连接…", "info"] };

const view = $("#view");
let cleanup = () => {};

/* ---------- 主题 ---------- */
// 存储键由 visoul.* 改为 visual.*；读取时兼容旧键，已保存的主题偏好不丢
const pref = {
  get: (k) => localStorage.getItem(`visual.${k}`) ?? localStorage.getItem(`visoul.${k}`),
  set: (k, v) => localStorage.setItem(`visual.${k}`, v),
};

function applyTheme() {
  const theme = pref.get("theme") || "dark";
  document.documentElement.dataset.theme = theme;
  const accent = pref.get("accent");
  if (accent) document.documentElement.style.setProperty("--accent-primary", accent);
  const btn = $("#theme-btn");
  btn.innerHTML = icon(theme === "dark" ? "sun" : "moon", 16);
  btn.title = theme === "dark" ? "切换到浅色主题" : "切换到深色主题";
  btn.setAttribute("aria-label", btn.title);
}

window.addEventListener("visual:preferences", applyTheme);

function buildShell() {
  $("#brand").innerHTML = `${LOGO}<span class="brand-name">视觉实验室</span><span class="brand-sub">SOP 合规</span>`;
  $("#nav").innerHTML = NAV.map(([k, t, ic]) => `<a href="#/${k}" data-nav="${k}">${icon(ic, 18)}<span>${t}</span></a>`).join("");
  $("#accent-menu").innerHTML = `<div class="menu-title">主题色</div><div class="swatches">${ACCENTS.map(([c, n]) => `<button class="swatch" style="--c:${c}" data-accent="${c}" title="${n}" aria-label="主题色 ${n}"></button>`).join("")}</div>`;
  $("#accent-btn").innerHTML = icon("palette", 16);
  $("#nav-toggle").innerHTML = icon("menu", 18);

  $("#theme-btn").onclick = () => {
    pref.set("theme", document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    applyTheme();
  };
  const menu = $("#accent-menu");
  let offEsc = () => {};
  const closeMenu = () => { menu.hidden = true; $("#accent-btn").setAttribute("aria-expanded", "false"); offEsc(); };
  $("#accent-btn").onclick = (e) => {
    e.stopPropagation();
    if (!menu.hidden) return closeMenu();
    menu.hidden = false;
    $("#accent-btn").setAttribute("aria-expanded", "true");
    offEsc = onEsc(closeMenu);
  };
  menu.onclick = (e) => {
    const b = e.target.closest("[data-accent]");
    if (!b) return;
    pref.set("accent", b.dataset.accent);
    applyTheme();
    closeMenu();
  };
  document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); });

  const app = $("#app");
  let offNav = () => {};
  const closeNav = () => { app.classList.remove("nav-open"); offNav(); };
  $("#nav-toggle").onclick = () => { app.classList.add("nav-open"); offNav = onEsc(closeNav); };
  $("#nav-scrim").onclick = closeNav;
  $("#nav").onclick = closeNav;
}

function paintConn() {
  const [text, tone] = CONN[store.conn];
  $("#conn").className = `conn tone-${tone}`;
  $("#conn").innerHTML = `<i class="dot dot-${tone}"></i><span>${text}</span>`;
}

/* ---------- 路由 ---------- */
function parseHash() {
  const [path, qs = ""] = (location.hash.slice(2) || "overview").split("?");
  const [name, ...rest] = path.split("/");
  return { name: ROUTES[name] ? name : "overview", params: { id: rest[0] ? decodeURIComponent(rest[0]) : "", ...Object.fromEntries(new URLSearchParams(qs)) } };
}

function route() {
  const { name, params } = parseHash();
  cleanup();
  $("#page-title").textContent = TITLES[name];
  document.title = `${TITLES[name]} · 视觉实验室`;
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const on = a.dataset.nav === name || (name === "labeling" && a.dataset.nav === "samples");
    a.classList.toggle("on", on);
    on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  });
  const page = document.createElement("div");
  page.className = `page page-${name}`;
  view.replaceChildren(page); // 每次换新容器，旧页面的事件监听随之释放
  cleanup = ROUTES[name].render(page, params) || (() => {});
  view.scrollTop = 0;
}

async function start() {
  buildShell();
  applyTheme();
  paintConn();
  const tickClock = () => ($("#clock").textContent = fmtClock());
  tickClock();
  setInterval(tickClock, 1000);
  subscribe((t) => t === "conn" && paintConn());
  window.addEventListener("hashchange", route);
  // 认证模式:未登录先给登录页,其余 API 由后端 401 兜底
  let needsLogin = false;
  try {
    const health = await fetch("/api/health").then((r) => r.json());
    if (health?.auth) {
      await api.me(); // 任何失败(401/网络抖动)都视为未登录,避免掉进错误块
      needsLogin = false;
    }
  } catch (e) {
    needsLogin = true;
  }
  if (needsLogin) {
    $("#page-title").textContent = "登录";
    login.render(view, {}, () => location.reload());
    return;
  }
  try {
    await bootstrap();
  } catch (e) {
    view.innerHTML = `<div class="page">${errorBlock(e.message)}</div>`;
    view.querySelector("[data-retry]").onclick = () => location.reload();
    return;
  }
  connect();
  mountAssistant();
  route();
}

start();
