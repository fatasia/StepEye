import { esc } from "../util.js";

const STATES = { draft: "草稿", published: "已发布", archived: "已归档" };
const DRAFT_STATES = { draft: "草稿", confirmed: "已确认", discarded: "已弃用" };

function stepZones(step) {
  const mode = ["all", "any", "seq"].find((k) => step.evidence[k]?.length);
  return (step.evidence[mode] || [])
    .map((cond) => Object.values(cond)[0])
    .filter((body) => body && typeof body.zone === "string")
    .map((body) => body.zone);
}

function withZones(steps, mapping) {
  return steps.map((step) => {
    const target = mapping[step.id];
    if (!target) return step;
    const mode = ["all", "any", "seq"].find((k) => step.evidence[k]?.length);
    const conds = (step.evidence[mode] || []).map((cond) => {
      const [kind, body] = Object.entries(cond)[0];
      return body && typeof body.zone === "string"
        ? { [kind]: { ...body, zone: target } }
        : cond;
    });
    return { ...step, evidence: { [mode]: conds } };
  });
}

export function render(root) {
  let dead = false;
  let role = null;
  let busy = false;
  const controller = new AbortController();
  root.innerHTML = `<div class="documents-page"><header><div><span>知识与作业依据</span><h2>文档中心</h2><p>保留原文件与历史版本；助手引用可追溯到具体段落。</p></div></header><p data-doc-message role="status"></p><form class="document-search"><input name="q" maxlength="200" required placeholder="搜索操作规程、设备或产品资料" aria-label="检索文档"><button class="btn" type="submit">检索</button><button class="btn" type="button" data-doc-refresh>全部版本</button></form><div data-doc-results></div><details data-doc-upload hidden><summary>上传文档 / 新版本</summary><form class="document-upload"><label>文档标题<input name="title" required maxlength="200"></label><label>已有文档 ID（留空新建）<input name="document_id" maxlength="100"></label><label>关联工位<input name="station" maxlength="200"></label><label>关联设备<input name="device" maxlength="200"></label><label>关联产品<input name="product" maxlength="200"></label><label>文件<input name="file" type="file" accept=".txt,.md,.pdf,.docx" required></label><p>最多10MB。文本须UTF-8；扫描PDF需先OCR。上传为草稿，管理员发布后只读用户可见。</p><button class="btn btn-primary" type="submit">上传草稿</button></form></details><div data-doc-list></div><section class="document-detail" data-doc-detail hidden></section><section data-sop-section hidden><h3>SOP 步骤草稿</h3><p data-doc-impact role="status"></p><div data-sop-drafts></div></section></div>`;
  const message = (value) => {
    if (!dead) root.querySelector("[data-doc-message]").textContent = value;
  };
  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（${response.status}）`);
    return data;
  }
  async function action(fn) {
    if (busy || dead) return;
    busy = true;
    root.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });
    try {
      await fn();
    } catch (error) {
      if (error.name !== "AbortError") message(error.message);
    } finally {
      busy = false;
      if (!dead)
        root.querySelectorAll("button").forEach((button) => {
          button.disabled = false;
        });
    }
  }
  async function detail(id) {
    const data = await request(`/api/documents/versions/${encodeURIComponent(id)}`);
    if (dead) return;
    const draftForm = role !== "viewer"
      ? `<form class="document-draft"><input name="station" required maxlength="160" placeholder="目标工位 id"><button class="btn" type="submit" data-doc-draft>生成步骤草稿</button></form>`
      : "";
    const target = root.querySelector("[data-doc-detail]");
    target.hidden = false;
    target.innerHTML = `<h3>${esc(data.version.title)} · v${Number(data.version.version)}</h3><p>${esc(data.version.document_id)}</p><a class="btn" href="/api/documents/versions/${encodeURIComponent(id)}/original" download>下载原文件</a>${draftForm}${data.paragraphs.map((p) => `<article><small>段落 ${Number(p.paragraph)} · 提取文本行 ${Number(p.line_start)}–${Number(p.line_end)}</small><pre>${esc(p.text)}</pre></article>`).join("")}`;
    const form = target.querySelector(".document-draft");
    if (form)
      form.onsubmit = (event) => {
        event.preventDefault();
        action(async () => {
          await request("/api/sop/links/drafts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              version_id: id,
              station_id: event.currentTarget.elements.station.value.trim(),
            }),
          });
          root.querySelector("[data-sop-section]").hidden = false;
          await loadDrafts();
          message("步骤草稿已生成，请在下方映射区域后确认。");
        });
      };
  }
  function draftCard(d) {
    const editable = d.status === "draft" && role !== "viewer";
    const steps = d.steps
      .map((s) => {
        const zones = stepZones(s);
        const zoneText = zones.length
          ? zones
              .map(
                (z) =>
                  `区域 <code>${esc(z)}</code>${
                    editable && z.startsWith("待映射:")
                      ? `<input data-zone-for="${esc(s.id)}" maxlength="100" placeholder="工位区域 id">`
                      : ""
                  }`,
              )
              .join(" ")
          : "";
        return `<li><b>${esc(s.id)}</b> ${esc(s.name || "")}${zoneText ? ` · ${zoneText}` : ""}</li>`;
      })
      .join("");
    const ops =
      d.status === "draft" && role !== "viewer"
        ? `<button class="btn" data-draft-save="${esc(d.id)}">保存区域映射</button><button class="btn" data-draft-confirm="${esc(d.id)}">确认</button><button class="btn" data-draft-discard="${esc(d.id)}">弃用</button>`
        : d.status === "confirmed" && role !== "viewer"
          ? `<a class="btn" href="/api/sop/links/drafts/${esc(d.id)}/recipe-patch" download>导出配方步骤</a>`
          : "";
    return `<article class="sop-draft" data-draft="${esc(d.id)}"><b>${esc(d.station_id)}</b><small>${DRAFT_STATES[d.status] || esc(d.status)} · 文档 v${Number(d.version_number)} · ${esc(d.actor)}</small><ul>${steps}</ul>${ops}</article>`;
  }
  async function loadDrafts() {
    const data = await request("/api/sop/links/drafts");
    if (dead) return;
    const box = root.querySelector("[data-sop-drafts]");
    box.innerHTML =
      data.items.map(draftCard).join("") ||
      "<p>还没有步骤草稿。在上方文档详情里点「生成步骤草稿」。</p>";
    box.querySelectorAll("[data-draft-save]").forEach((button) => {
      button.onclick = () =>
        action(async () => {
          const card = button.closest("[data-draft]");
          const listing = await request("/api/sop/links/drafts");
          const draft = listing.items.find((x) => x.id === button.dataset.draftSave);
          const mapping = {};
          card.querySelectorAll("[data-zone-for]").forEach((input) => {
            if (input.value.trim()) mapping[input.dataset.zoneFor] = input.value.trim();
          });
          if (!Object.keys(mapping).length) {
            message("请先填写至少一个工位区域 id。");
            return;
          }
          await request(`/api/sop/links/drafts/${encodeURIComponent(button.dataset.draftSave)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ steps: withZones(draft.steps, mapping) }),
          });
          await loadDrafts();
          message("区域映射已保存。");
        });
    });
    box.querySelectorAll("[data-draft-confirm]").forEach((button) => {
      button.onclick = () =>
        action(async () => {
          await request(`/api/sop/links/drafts/${encodeURIComponent(button.dataset.draftConfirm)}/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          });
          await loadDrafts();
          message("草稿已确认；写入工位规则请走配方版本。");
        });
    });
    box.querySelectorAll("[data-draft-discard]").forEach((button) => {
      button.onclick = () =>
        action(async () => {
          await request(`/api/sop/links/drafts/${encodeURIComponent(button.dataset.draftDiscard)}/discard`, {
            method: "POST",
          });
          await loadDrafts();
          message("草稿已弃用。");
        });
    });
  }
  async function load() {
    const data = await request("/api/documents");
    if (dead) return;
    root.querySelector("[data-doc-list]").innerHTML =
      `<div class="document-table"><table><thead><tr><th>文档 / 版本</th><th>关联</th><th>状态</th><th>操作</th></tr></thead><tbody>${data.items.map((v) => `<tr><td><b>${esc(v.title)}</b><small>v${Number(v.version)} · ${esc(v.filename)}</small><small>${esc(v.document_id)}</small></td><td>${[v.station, v.device, v.product].filter(Boolean).map(esc).join(" / ") || "—"}</td><td>${STATES[v.status] || esc(v.status)}</td><td><button class="btn" data-doc-open="${esc(v.id)}">阅读</button>${role !== "viewer" ? `<button class="btn" data-doc-impact="${esc(v.document_id)}">影响检查</button>` : ""}${role === "admin" ? `<button class="btn" data-doc-status="${esc(v.id)}" data-next="${v.status === "published" ? "archived" : "published"}">${v.status === "published" ? "归档" : "发布"}</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="4">暂无可见文档</td></tr>'}</tbody></table></div>`;
    root.querySelectorAll("[data-doc-open]").forEach((button) => {
      button.onclick = () => action(() => detail(button.dataset.docOpen));
    });
    root.querySelectorAll("[data-doc-impact]").forEach((button) => {
      button.onclick = () =>
        action(async () => {
          const impact = await request(
            `/api/sop/links/impact?document_id=${encodeURIComponent(button.dataset.docImpact)}`,
          );
          if (dead) return;
          root.querySelector("[data-sop-section]").hidden = false;
          await loadDrafts();
          const hits = impact.affected || [];
          root.querySelector("[data-doc-impact]").textContent = hits.length
            ? `最新版本 v${Number(impact.latest_version)} 变动影响 ${hits.length} 段已确认草稿；不会自动修改规则。`
            : `最新版本 v${Number(impact.latest_version)} 与已确认草稿一致。`;
        });
    });
    root.querySelectorAll("[data-doc-status]").forEach((button) => {
      button.onclick = () =>
        action(async () => {
          if (
            role !== "admin" ||
            !confirm(`${button.textContent}此版本？发布新版本会归档同文档的旧发布版本。`)
          )
            return;
          await request(`/api/documents/versions/${encodeURIComponent(button.dataset.docStatus)}/status`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: button.dataset.next }),
          });
          root.querySelector("[data-doc-detail]").hidden = true;
          root.querySelector("[data-doc-results]").replaceChildren();
          await load();
          message("文档状态已更新。");
        });
    });
  }
  root.querySelector(".document-search").onsubmit = (event) => {
    event.preventDefault();
    action(async () => {
      const q = event.currentTarget.elements.q.value.trim();
      const data = await request(`/api/documents/search?q=${encodeURIComponent(q)}`);
      if (dead) return;
      root.querySelector("[data-doc-results]").innerHTML =
        data.items
          .map(
            (hit) =>
              `<article class="document-hit"><b>${esc(hit.document)} · v${Number(hit.version)} · 段落 ${Number(hit.paragraph)}</b><pre>${esc(hit.text)}</pre></article>`,
          )
          .join("") || "<p>没有匹配段落。</p>";
    });
  };
  root.querySelector(".document-upload").onsubmit = (event) => {
    event.preventDefault();
    if (!["operator", "admin"].includes(role)) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    if (!data.get("document_id")) data.delete("document_id");
    if (form.elements.file.files[0]?.size > 10 * 1024 * 1024) {
      message("文件超过10MB");
      return;
    }
    action(async () => {
      message("正在解析并保存草稿…");
      await request("/api/documents", { method: "POST", body: data });
      if (dead) return;
      form.reset();
      await load();
      message("草稿已保存，等待管理员发布。");
    });
  };
  root.querySelector("[data-doc-refresh]").onclick = () => action(load);
  action(async () => {
    const me = await request("/api/auth/me");
    if (!["viewer", "operator", "admin"].includes(me.role)) throw new Error("当前账号无权访问文档中心");
    role = me.role;
    if (dead) return;
    root.querySelector("[data-doc-upload]").hidden = role === "viewer";
    if (role !== "viewer") {
      root.querySelector("[data-sop-section]").hidden = false;
      await loadDrafts();
    }
    await load();
  });
  return () => {
    dead = true;
    controller.abort();
  };
}
