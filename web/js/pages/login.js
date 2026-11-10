import { api } from "../api.js";
import { icon, LOGO } from "../icons.js";
import { errorBlock } from "../ui.js";
import { $, esc } from "../util.js";

/** 登录页:提交成功后整页重载,让 bootstrap 重新拉数据。 */
export function render(root, params, onOk) {
  root.innerHTML = `
    <div class="login-wrap">
      <section class="panel login-panel">
        <header class="panel-head"><div class="head-left"><h2>登录</h2><span class="login-brand">${LOGO}<span class="brand-name">StepEye</span></span></div></header>
        <div class="panel-body" data-r="body">
          <label class="field login-field"><span>用户名</span><input id="l-user" autocomplete="username" autofocus></label>
          <label class="field login-field"><span>密码</span><input id="l-pass" type="password" autocomplete="current-password"></label>
          <button class="btn btn-primary" id="l-go">${icon("check", 14)}登录</button>
          <p class="hint muted">没有账号?请联系管理员运行 <code>python -m visual passwd 用户名</code> 创建。</p>
        </div>
      </section>
    </div>`;
  const go = async () => {
    const body = $("[data-r='body']", root);
    try {
      await api.login($("#l-user", root).value.trim(), $("#l-pass", root).value);
      (onOk || (() => location.reload()))();
    } catch (e) {
      body.insertAdjacentHTML("beforeend", errorBlock(e.message));
      const rb = body.querySelector("[data-retry]");
      if (rb) rb.onclick = go;
    }
  };
  $("#l-go", root).onclick = go;
  root.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  root.querySelector("#l-user").focus();
}
