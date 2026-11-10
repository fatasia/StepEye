import { esc } from "../util.js";

const PURPOSES = { chat: "平台助手", vlm: "视觉辅助", image: "图片生成" };

/** Admin-only service editor. Secrets exist only in the password input until submit. */
export function mountAIServices(root, { canAdmin }) {
  let dead = false;
  let busy = false;
  let services = [];
  let bindings = {};
  const controller = new AbortController();
  root.innerHTML = `<div class="ai-services"><header><div><h3>AI 服务</h3><p>登记服务后，分别指定助手、视觉辅助与图片生成使用的模型。</p></div><button class="btn" data-ai-refresh>刷新</button></header><p role="status" data-ai-message></p><div data-ai-list></div><form data-ai-service class="ai-service-form"><h4>新增 / 编辑服务</h4><input name="id" type="hidden"><label>服务名称<input name="name" required maxlength="100" placeholder="例如：本地 Qwen"></label><label>API 地址<input name="base_url" type="url" required maxlength="1000" placeholder="https://example.com/v1"></label><label>模型标识<input name="model" required maxlength="200" placeholder="服务提供的模型名称"></label><label>API Key<input name="api_key" type="password" maxlength="4096" autocomplete="new-password" placeholder="编辑时留空保留已有密钥"></label><div class="ai-service-actions"><button class="btn btn-primary" type="submit">保存服务</button><button class="btn" type="button" data-ai-new>取消编辑 / 新增</button></div></form><form data-ai-bindings class="ai-service-bindings"><h4>用途绑定</h4><p class="settings-note">图片生成使用 OpenAI 兼容 images/generations，需返回 b64_json；在样本库选择“已配置服务”发起实际文生图。参考图不发送。</p>${Object.entries(
    PURPOSES,
  )
    .map(([key, label]) => `<label>${label}<select name="${key}"></select></label>`)
    .join("")}<button class="btn" type="submit">保存用途绑定</button></form></div>`;
  const form = root.querySelector("[data-ai-service]");
  const bindingForm = root.querySelector("[data-ai-bindings]");
  const message = (text) => {
    if (!dead) root.querySelector("[data-ai-message]").textContent = text;
  };
  async function request(url, options = {}) {
    if (!canAdmin()) throw new Error("仅管理员可以管理 AI 服务。");
    const response = await fetch(url, { ...options, signal: controller.signal });
    const data = await response.json().catch(() => ({}));
    // Do not echo provider response bodies: they may contain credentials or full URLs.
    if (!response.ok) throw new Error(`操作未完成（HTTP ${response.status}），请检查权限与服务配置。`);
    return data;
  }
  function setBusy(value) {
    busy = value;
    root.querySelectorAll("button").forEach((button) => {
      button.disabled = value;
    });
  }
  function edit(service = {}) {
    for (const key of ["id", "name", "base_url", "model"]) form.elements[key].value = service[key] || "";
    form.elements.api_key.value = "";
    form.elements.api_key.placeholder = service.api_key_set
      ? "已保存密钥；留空保留"
      : "填写服务密钥；本地服务可留空";
    form.querySelector("h4").textContent = service.id ? `编辑：${service.name}` : "新增服务";
  }
  async function load() {
    const data = await request("/api/ai/services");
    if (dead) return;
    services = Array.isArray(data.services) ? data.services : [];
    bindings = data.bindings || {};
    root.querySelector("[data-ai-list]").innerHTML =
      services
        .map(
          (s, i) =>
            `<article class="ai-service-row"><div><b>${esc(s.name)}</b><small>${esc(s.model)} · ${s.api_key_set ? "密钥已保存" : "未保存密钥"}</small></div><div><button class="btn" data-ai-edit="${i}">编辑</button><button class="btn" data-ai-test="${i}" data-purpose="chat">测试对话</button><button class="btn" data-ai-test="${i}" data-purpose="vlm">测试视觉</button></div></article>`,
        )
        .join("") || '<p class="settings-note">还没有服务，先填写下面的名称、地址和模型。</p>';
    for (const key of Object.keys(PURPOSES)) {
      bindingForm.elements[key].innerHTML =
        `<option value="">不使用</option>${services.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} · ${esc(s.model)}</option>`).join("")}`;
      bindingForm.elements[key].value = bindings[key] || "";
      bindingForm.elements[key].disabled = key === "image" && data.image_supported !== true;
    }
    root.querySelectorAll("[data-ai-edit]").forEach((button) => {
      button.onclick = () => edit(services[Number(button.dataset.aiEdit)]);
    });
    root.querySelectorAll("[data-ai-test]").forEach((button) => {
      button.onclick = () =>
        action(async () => {
          const service = services[Number(button.dataset.aiTest)];
          message("正在测试连接…");
          const result = await request(`/api/ai/services/${encodeURIComponent(service.id)}/test`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ purpose: button.dataset.purpose }),
          });
          const ok = result.ok === true || result.status === "ok";
          message(
            ok ? "连接测试通过；不代表识别准确率或持续可用性。" : "测试未报告成功，请检查模型、地址与密钥。",
          );
        });
    });
  }
  async function action(fn) {
    if (busy || dead || !canAdmin()) return;
    setBusy(true);
    try {
      await fn();
    } catch (error) {
      if (error.name !== "AbortError") message(error.message);
    } finally {
      if (!dead) setBusy(false);
    }
  }
  form.onsubmit = (event) => {
    event.preventDefault();
    if (busy || !canAdmin()) return;
    const id =
      form.elements.id.value ||
      `service-${crypto.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}`;
    const body = {
      name: form.elements.name.value.trim(),
      base_url: form.elements.base_url.value.trim(),
      model: form.elements.model.value.trim(),
    };
    if (form.elements.api_key.value) body.api_key = form.elements.api_key.value;
    form.elements.api_key.value = "";
    action(async () => {
      await request(`/api/ai/services/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      delete body.api_key;
      if (dead) return;
      edit();
      await load();
      message("服务已保存，尚未测试连接。");
    }).finally(() => {
      delete body.api_key;
    });
  };
  bindingForm.onsubmit = (event) => {
    event.preventDefault();
    action(async () => {
      const body = Object.fromEntries(
        Object.keys(PURPOSES).map((key) => [key, bindingForm.elements[key].value || null]),
      );
      await request("/api/ai/bindings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      message("用途绑定已保存。");
    });
  };
  root.querySelector("[data-ai-refresh]").onclick = () => action(load);
  root.querySelector("[data-ai-new]").onclick = () => edit();
  action(load);
  return () => {
    dead = true;
    controller.abort();
    form.elements.api_key.value = "";
    root.replaceChildren();
  };
}
