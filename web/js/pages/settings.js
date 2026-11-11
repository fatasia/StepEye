import { openUserScope } from "./user-scope.js";
import { mountAIServices } from "./ai-services.js";
import { mountSetup } from './setup.js';
import { icon } from "../icons.js";
import { $, esc } from "../util.js";
import { openDrawer, toast } from "../ui.js";

const ROLES = { viewer: "只读", operator: "操作员", admin: "管理员" };
const ACCENTS = [
  ["#3ec6c1", "青"],
  ["#65aee8", "蓝"],
  ["#59c58d", "绿"],
  ["#d8ac52", "琥珀"],
  ["#b08cf0", "紫"],
];
async function request(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（HTTP ${response.status}）`);
  return data;
}

export function render(root, params = {}) {
  root.innerHTML = `<div class="settings-workspace"><header class="settings-head"><span class="settings-kicker">工作空间 / 设置</span><h2>把使用习惯和访问权限，放在一起。</h2><p>外观保存在当前浏览器。账号与服务状态来自正在运行的 StepEye。</p></header>
    <div class="settings-layout"><nav class="settings-nav" aria-label="设置分类"><button data-settings-tab="general">${icon("palette", 16)}通用外观</button><button data-settings-tab="ai" data-settings-admin hidden>AI 服务</button><button data-settings-tab="users" data-settings-admin hidden>${icon("config", 16)}用户与权限</button><button data-settings-tab="notifications" data-settings-admin hidden>${icon("inbox", 16)}通知状态</button><button data-settings-tab="logs" data-settings-admin hidden>${icon("clock", 16)}操作日志</button><button data-settings-tab="operations">${icon("live", 16)}运行状态</button></nav>
      <div class="settings-content"><div class="settings-error" role="alert" data-settings-error hidden></div>
        <section data-settings-panel="general"><header><h3>通用外观</h3><p>即时生效，刷新后保留；不会改变其他用户的浏览器。</p></header><div class="settings-option"><div><b>界面主题</b><p>与顶部主题开关同步。</p></div><div class="settings-theme" role="group" aria-label="界面主题"><button data-settings-theme="dark">深色</button><button data-settings-theme="light">浅色</button></div></div><div class="settings-option"><div><b>强调色</b><p>用于选中状态、主要按钮与标记。</p></div><div class="settings-accents">${ACCENTS.map(([value, name]) => `<button data-settings-accent="${value}" style="--settings-accent:${value}" aria-label="强调色${name}" title="${name}"></button>`).join("")}</div></div><p class="settings-note" data-settings-session>正在读取访问模式…</p></section>
        <section data-settings-panel="ai" hidden><div data-ai-services></div></section><section data-settings-panel="users" hidden><header><div><h3>用户与权限</h3><p>角色修改必须同时设置新密码，旧会话与 MCP 授权随之撤销。</p></div><button class="btn btn-primary" data-settings-add-user>新增用户</button></header><div data-settings-users></div><p class="settings-note">只读：查看；操作员：业务操作；管理员：账号与系统管理。至少保留一个管理员。</p></section>
        <section data-settings-panel="notifications" hidden><header><div><h3>通知状态</h3><p>查看已配置渠道的运行状态；地址与密钥不在这里显示。</p></div><button class="btn" data-settings-refresh-notifications>刷新</button></header><div data-settings-notifications></div><p class="settings-note">在下方配置渠道或发送测试通知，已保存的密钥不回显。</p></section>
        <section data-settings-panel="logs" hidden><header><div><h3>操作日志</h3><p>仅记录业务写请求的账号、路由和响应状态，不含请求正文或密钥。</p></div><button class="btn" data-settings-refresh-logs>刷新</button></header><div data-settings-logs></div><button class="btn" data-settings-more-logs hidden>加载更早记录</button><p class="settings-note">这是请求级操作记录，不是系统日志或每条业务变更的前后对照。时间以 UTC 显示。</p></section><section data-settings-panel="operations" hidden><header><div><h3>运行状态</h3><p>按需查询，不增加后台监控线程。</p></div><button class="btn" data-settings-refresh-operations>刷新</button></header><div data-settings-operations></div><p class="settings-note">本页展示健康接口与已配置工位，不代表相机实时在线。系统日志、备份与恢复暂未在本页提供。</p></section>
      </div></div></div>`;
  let aiCleanup = null;
  let dead = false,
    me = null,
    health = null,
    users = [],
    drawer = null,
    currentTab = "general";
  const showError = (message) => {
    if (!dead) {
      const el = $("[data-settings-error]", root);
      el.hidden = !message;
      el.textContent = message;
    }
  };
  const isAdmin = () => me?.role === "admin";
  let logRows = [],
    logBefore = null,
    logBusy = false;
  function paintTheme() {
    root
      .querySelectorAll("[data-settings-theme]")
      .forEach((button) =>
        button.setAttribute(
          "aria-pressed",
          String(button.dataset.settingsTheme === document.documentElement.dataset.theme),
        ),
      );
    const accent =
      localStorage.getItem("visual.accent") ?? localStorage.getItem("visoul.accent") ?? ACCENTS[0][0];
    root
      .querySelectorAll("[data-settings-accent]")
      .forEach((button) =>
        button.setAttribute("aria-pressed", String(button.dataset.settingsAccent === accent)),
      );
  }
  function preference(name, value) {
    try {
      // 沿用 shell 的存储键，通过事件让顶部开关与当前页面同步更新。
      localStorage.setItem(`visual.${name}`, value);
      window.dispatchEvent(new Event("visual:preferences"));
      paintTheme();
    } catch {
      showError("浏览器未允许保存偏好，请检查隐私或存储设置。");
    }
  }
  async function userList() {
    if (!isAdmin()) return;
    const rows = await request("/api/users");
    if (dead) return;
    users = rows;
    $("[data-settings-users]", root).innerHTML =
      `<div class="settings-table-wrap"><table class="settings-table"><thead><tr><th>用户名</th><th>角色</th><th>数据范围</th><th>创建 / 更新</th><th>操作</th></tr></thead><tbody>${rows.map((user) => `<tr data-user-row="${esc(user.username)}"><td><b>${esc(user.username)}</b>${user.username === me.username ? "<small>当前账号</small>" : ""}</td><td>${ROLES[user.role] || esc(user.role)}</td><td>${user.project_ids == null ? "全部数据" : user.project_ids.length ? `${user.project_ids.length} 个项目` : "无数据权限"}</td><td>${esc(user.created || "—")}</td><td><button class="settings-link" data-user-edit="${esc(user.username)}">编辑 / 重设密码</button>${user.role !== "admin" ? `<button class="settings-link" data-user-scope="${esc(user.username)}">数据范围</button>` : ""}<button class="settings-link danger" data-user-delete="${esc(user.username)}">删除</button></td></tr>`).join("")}</tbody></table></div>`;
    root
      .querySelectorAll("[data-user-edit]")
      .forEach(
        (button) =>
          (button.onclick = () => userForm(users.find((u) => u.username === button.dataset.userEdit))),
      );
    root.querySelectorAll("[data-user-scope]").forEach(button => {
      button.onclick = () => {
        drawer?.close();
        drawer = openUserScope(users.find(u => u.username === button.dataset.userScope), request, userList);
      };
    });
    root
      .querySelectorAll("[data-user-delete]")
      .forEach((button) => (button.onclick = () => deleteUser(button.dataset.userDelete, button)));
  }
  function userForm(existing = null) {
    if (!isAdmin()) return;
    drawer?.close();
    const d = (drawer = openDrawer({
      title: existing ? "编辑账号与重设密码" : "新增用户",
      html: `<form class="settings-user-form"><label>用户名<input name="username" required maxlength="64" value="${esc(existing?.username || "")}" ${existing ? "readonly" : ""} autocomplete="off"></label><label>角色<select name="role">${Object.entries(
        ROLES,
      )
        .map(
          ([value, name]) =>
            `<option value="${value}" ${value === (existing?.role || "viewer") ? "selected" : ""}>${name}</option>`,
        )
        .join(
          "",
        )}</select></label><label>${existing ? "新密码" : "初始密码"}<input name="password" type="password" required minlength="8" maxlength="256" autocomplete="new-password"></label><p>密码至少 8 位。${existing ? "保存后撤销该账号已有会话和 MCP 令牌；编辑当前账号后需重新登录。" : "用户名支持中文、字母、数字、点、下划线和连字符。"}</p><div class="settings-error" data-user-form-error role="alert" hidden></div><button class="btn btn-primary" type="submit">${existing ? "确认重设并保存" : "创建用户"}</button></form>`,
      onClose: () => {
        const input = d.body.querySelector("[name=password]");
        if (input) input.value = "";
      },
    }));
    const form = $("form", d.body);
    form.onsubmit = async (event) => {
      event.preventDefault();
      const name = form.elements.username.value.trim();
      if (!existing && users.some((u) => u.username === name)) {
        const message = $("[data-user-form-error]", form);
        message.hidden = false;
        message.textContent = "该用户名已存在，请使用列表中的编辑操作。";
        return;
      }
      if (existing && !window.confirm(`确认重设 ${name} 的密码和角色？该账号的旧会话与 MCP 授权将撤销。`))
        return;
      const button = $("button[type=submit]", form);
      button.disabled = true;
      const body = JSON.stringify({
        username: name,
        role: form.elements.role.value,
        password: form.elements.password.value,
      });
      // 密码不留在组件状态、提示或日志中，发出请求后立即清空输入。
      form.elements.password.value = "";
      try {
        await request("/api/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        });
        if (dead) return;
        d.close();
        if (existing?.username === me.username) {
          location.reload();
          return;
        }
        await userList();
        toast(existing ? "账号已更新" : "用户已创建");
      } catch (e) {
        if (!dead) {
          const message = $("[data-user-form-error]", form);
          message.hidden = false;
          message.textContent = e.message;
        }
      } finally {
        button.disabled = false;
      }
    };
  }
  async function deleteUser(username, button) {
    if (
      !isAdmin() ||
      !window.confirm(`删除用户 ${username}？该账号将无法登录，已有会话与 MCP 授权会被撤销。`)
    )
      return;
    button.disabled = true;
    showError("");
    try {
      await request(`/api/users/${encodeURIComponent(username)}`, { method: "DELETE" });
      if (dead) return;
      if (username === me.username) {
        location.reload();
        return;
      }
      await userList();
      toast("用户已删除");
    } catch (e) {
      showError(e.message);
      button.disabled = false;
    }
  }
  async function notifications() {
    if (!isAdmin()) return;
    const status = await request("/api/notifications/status");
    if (dead) return;
    const channels = status.channels || [];
    $("[data-settings-notifications]", root).innerHTML =
      `<div class="settings-stats"><div><span>渠道配置</span><b>${status.configured ? "已配置" : "未配置"}</b></div><div><span>工作进程</span><b>${{ idle: "空闲", running: "运行中", closed: "已关闭" }[status.worker] || esc(status.worker)}</b></div><div><span>排队任务</span><b>${Number(status.queued) || 0}</b></div></div>${(status.config_errors || []).map((v) => `<p class="settings-error">${esc(v)}</p>`).join("")}<div class="settings-channel-list">${channels.length ? channels.map((c) => `<div><b>${esc(c.name || c.id || "通知渠道")}</b><span>成功 ${Number(c.delivered) || 0} · 失败 ${Number(c.failed) || 0} · ${esc(c.status || "未发送")}</span></div>`).join("") : '<p class="settings-note">尚无可运行通知渠道；当前没有常驻发送进程。</p>'}</div>`;
  }
  async function logs(more = false) {
    if (!isAdmin() || logBusy) return;
    logBusy = true;
    const next = $("[data-settings-more-logs]", root);
    const refresh = $("[data-settings-refresh-logs]", root);
    next.disabled = refresh.disabled = true;
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (more && logBefore !== null) query.set("before", String(logBefore));
      const data = await request(`/api/operations/logs?${query}`);
      if (dead) return;
      logRows = more ? [...logRows, ...data.items] : data.items;
      logBefore = logRows.at(-1)?.id ?? null;
      $("[data-settings-logs]", root).innerHTML =
        `<div class="settings-table-wrap"><table class="settings-table"><thead><tr><th>时间（UTC）</th><th>账号</th><th>操作</th><th>状态</th></tr></thead><tbody>${logRows.map((r) => `<tr><td>${esc(r.ts)}</td><td>${esc(r.actor)}</td><td>${esc(r.method)} ${esc(r.route)}</td><td>${Number(r.status)}</td></tr>`).join("") || '<tr><td colspan="4">尚无操作记录</td></tr>'}</tbody></table></div><p class="settings-note">已显示 ${logRows.length} 条 · 保留上限 ${Number(data.retained_limit) || 0} 条 · 丢弃计数 ${Number(data.dropped) || 0}</p>`;
      next.hidden = data.items.length < 100 || logRows.length >= 500;
    } finally {
      logBusy = false;
      if (!dead) next.disabled = refresh.disabled = false;
    }
  }

  async function operations() {
    const [status, stations] = await Promise.all([request("/api/health"), request("/api/stations")]);
    if (dead) return;
    $("[data-settings-operations]", root).innerHTML =
      `<div class="settings-stats"><div><span>服务响应</span><b>${status.ok ? "正常" : "异常"}</b></div><div><span>认证</span><b>${status.auth ? "已启用" : "未启用"}</b></div><div><span>已配置工位</span><b>${Number(status.stations) || 0}</b></div></div><div class="settings-table-wrap"><table class="settings-table"><thead><tr><th>工位</th><th>产线</th><th>来源类型</th></tr></thead><tbody>${
        stations
          .map((station) => {
            const meta = station.meta || {};
            return `<tr><td>${esc(meta.name || meta.id || "未命名")}</td><td>${esc(meta.line || "—")}</td><td>${esc(meta.source || "未知")}</td></tr>`;
          })
          .join("") || '<tr><td colspan="3">尚未配置工位</td></tr>'
      }</tbody></table></div>`;
  }
  async function tab(name) {
    if (["users", "notifications", "logs", "ai"].includes(name) && !isAdmin()) return;
    if (name !== 'ai' && aiCleanup) {
      aiCleanup();
      aiCleanup = null;
    }
    currentTab = name;
    showError("");
    root
      .querySelectorAll("[data-settings-panel]")
      .forEach((panel) => (panel.hidden = panel.dataset.settingsPanel !== name));
    root
      .querySelectorAll("[data-settings-tab]")
      .forEach((button) =>
        button.setAttribute("aria-current", button.dataset.settingsTab === name ? "page" : "false"),
      );
    try {
      if (name === "ai" && !aiCleanup)
        aiCleanup = mountAIServices(root.querySelector('[data-ai-services]'), { canAdmin: isAdmin });
      if (name === "users") await userList();
      if (name === "notifications") await notifications();
      if (name === "operations") await operations();
      if (name === "logs") await logs();
    } catch (e) {
      showError(e.message);
    }
  }
  root
    .querySelectorAll("[data-settings-theme]")
    .forEach((button) => (button.onclick = () => preference("theme", button.dataset.settingsTheme)));
  root
    .querySelectorAll("[data-settings-accent]")
    .forEach((button) => (button.onclick = () => preference("accent", button.dataset.settingsAccent)));
  root
    .querySelectorAll("[data-settings-tab]")
    .forEach((button) => (button.onclick = () => tab(button.dataset.settingsTab)));
  $("[data-settings-add-user]", root).onclick = () => userForm();
  $("[data-settings-refresh-notifications]", root).onclick = () => tab("notifications");
  $("[data-settings-refresh-operations]", root).onclick = () => tab("operations");
  $("[data-settings-refresh-logs]", root).onclick = () => tab("logs");
  $("[data-settings-more-logs]", root).onclick = () => logs(true).catch((e) => showError(e.message));
  // 顶部主题开关同样会修改根属性；仅观察，不启动轮询。
  const observer = new MutationObserver(paintTheme);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "style"] });
  paintTheme();
  tab("general");
  Promise.all([request("/api/auth/me"), request("/api/health")])
    .then(([user, status]) => {
      if (dead) return;
      me = user;
      health = status;
      root.querySelectorAll("[data-settings-admin]").forEach((el) => (el.hidden = !isAdmin()));
      root.querySelector('[data-settings-tab="users"]').hidden = !isAdmin() || !status.auth;
      if (params.tab) tab(params.tab);
      if (isAdmin()) mountSetup(root, status).catch(e=>showError(e.message));
      $("[data-settings-session]", root).textContent = !status.auth
        ? "当前为本机模式，可在下方启用账号登录。"
        : `当前账号：${user.username} · ${ROLES[user.role] || user.role}${isAdmin() ? "" : "。用户管理和通知状态仅管理员可见。"}`;
    })
    .catch((e) => showError(e.message));
  return () => {
    dead = true;
    observer.disconnect();
    aiCleanup?.();
    drawer?.close();
  };
}
