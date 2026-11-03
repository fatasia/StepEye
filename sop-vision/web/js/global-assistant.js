/** Global text-only assistant. No localStorage, automatic polling or page-content upload. */
export function mountAssistant(host = document.body) {
  const shell = document.createElement("div");
  shell.className = "global-assistant";
  shell.innerHTML = `<button class="assistant-launch" type="button" aria-label="打开 AI 助手" aria-expanded="false" hidden>AI 助手</button><aside class="assistant-panel" aria-label="AI 助手" hidden><header><div><b>StepEye 助手</b><small>查询平台资料，理解当前功能</small></div><button type="button" data-assistant-close aria-label="关闭助手">×</button></header><div class="assistant-messages" role="log" aria-live="polite"></div><p class="assistant-status" role="status"></p><form class="assistant-form"><label for="assistant-question">你的问题</label><textarea id="assistant-question" name="message" rows="3" maxlength="2000" required placeholder="例如：如何创建标注项目？"></textarea><div><button type="button" data-assistant-clear>清空</button><button type="button" data-assistant-cancel hidden>停止等待</button><button type="submit">发送</button></div></form></aside>`;
  host.append(shell);
  const launch = shell.querySelector(".assistant-launch");
  const panel = shell.querySelector(".assistant-panel");
  const log = shell.querySelector(".assistant-messages");
  const status = shell.querySelector(".assistant-status");
  const form = shell.querySelector("form");
  let dead = false;
  let allowed = false;
  let controller = null;
  let history = [];
  let generation = 0;
  const authController = new AbortController();
  function textMessage(role, content, sources = []) {
    const article = document.createElement("article");
    article.className = `assistant-message assistant-message-${role}`;
    const title = document.createElement("b");
    title.textContent = role === "user" ? "你" : "助手";
    const text = document.createElement("p");
    text.textContent = String(content).slice(0, 12000);
    article.append(title, text);
    if (sources.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `资料依据 · ${Math.min(sources.length, 8)}`;
      details.append(summary);
      for (const source of sources.slice(0, 8)) {
        const item = document.createElement("p");
        item.textContent = `${String(source.document || "资料").slice(0, 200)} · ${source.line_start ?? "?"}–${source.line_end ?? "?"}\n${String(source.text || "").slice(0, 1500)}`;
        details.append(item);
      }
      article.append(details);
    }
    log.append(article);
    while (log.children.length > 24) log.firstElementChild.remove();
    log.scrollTop = log.scrollHeight;
  }
  function busy(value) {
    form.querySelector("[type=submit]").disabled = value || !allowed;
    form.querySelector("[data-assistant-cancel]").hidden = !value;
    form.querySelector("[data-assistant-clear]").disabled = value;
    form.elements.message.disabled = value;
  }
  function cancel() {
    generation += 1;
    controller?.abort();
    controller = null;
    busy(false);
  }
  function close() {
    panel.hidden = true;
    launch.setAttribute("aria-expanded", "false");
    if (controller) {
      cancel();
      status.textContent = "已停止等待，服务端请求可能仍在结束处理中。";
    }
    launch.focus();
  }
  launch.onclick = () => {
    if (!allowed) return;
    if (!panel.hidden) {
      close();
      return;
    }
    panel.hidden = false;
    launch.setAttribute("aria-expanded", "true");
    if (!panel.hidden) form.elements.message.focus();
  };
  shell.querySelector("[data-assistant-close]").onclick = close;
  shell.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) close();
  });
  shell.querySelector("[data-assistant-cancel]").onclick = () => {
    cancel();
    status.textContent = "已停止等待，本次问题未加入后续对话。";
  };
  shell.querySelector("[data-assistant-clear]").onclick = () => {
    history = [];
    log.replaceChildren();
    status.textContent = "";
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (!allowed || controller || dead) return;
    const message = form.elements.message.value.trim();
    if (!message) return;
    const current = ++generation;
    controller = new AbortController();
    const requestController = controller;
    const timeout = setTimeout(() => requestController.abort(), 90000);
    busy(true);
    status.textContent = "正在查阅资料…";
    textMessage("user", message);
    try {
      const response = await fetch("/api/assistant/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: requestController.signal,
        body: JSON.stringify({
          message,
          history: history.slice(-12),
          context: { page: location.hash.split("?")[0].slice(0, 120) },
        }),
      });
      if (!response.ok) {
        if ([401, 403].includes(response.status)) allowed = false;
        throw new Error(`助手未能完成请求（HTTP ${response.status}）。`);
      }
      const data = await response.json();
      if (dead || current !== generation) return;
      if (typeof data.answer !== "string" || !data.answer.trim()) throw new Error("服务未返回回答。");
      const answer = data.answer.slice(0, 12000);
      textMessage("assistant", answer, Array.isArray(data.sources) ? data.sources : []);
      history = [
        ...history,
        { role: "user", content: message },
        { role: "assistant", content: answer.slice(0, 2000) },
      ].slice(-12);
      form.elements.message.value = "";
      status.textContent = data.mode === "retrieval" ? "资料检索结果" : "回答已完成，请结合资料依据核对。";
    } catch (error) {
      if (!dead && current === generation)
        status.textContent = error.name === "AbortError" ? "请求等待超时，可稍后重试。" : error.message;
    } finally {
      clearTimeout(timeout);
      if (!dead && current === generation) {
        controller = null;
        busy(false);
      }
    }
  };
  // Unknown or unauthenticated roles never expose a chat action.
  Promise.all(
    ["/api/health", "/api/auth/me"].map(async (url) => {
      const response = await fetch(url, { signal: authController.signal });
      if (!response.ok) throw new Error("未登录");
      return response.json();
    }),
  )
    .then(([health, me]) => {
      if (dead) return;
      allowed = health.auth === true && ["viewer", "operator", "admin"].includes(me.role);
      launch.hidden = !allowed;
      busy(false);
    })
    .catch(() => {
      allowed = false;
    });
  return () => {
    dead = true;
    cancel();
    authController.abort();
    history = [];
    shell.remove();
  };
}
