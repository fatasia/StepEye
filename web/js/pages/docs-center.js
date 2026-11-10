import { icon } from "../icons.js";
import { $, esc } from "../util.js";

/** 轻量 Markdown 渲染:先整体转义再按块解析,不引入第三方依赖。
 * 图片相对路径映射到 /api/project-docs/asset/,绝对 http/https 与锚点保持原样。 */
export function mdToHtml(src) {
  const lines = esc(src).replace(/\r\n/g, "\n").split("\n");
  let html = "", inCode = false, codeLang = "", listType = null, tableRows = [];
  const inline = (t) => t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, href) => {
      const url = /^(https?:|#|\/)/.test(href) ? href : `/api/project-docs/asset/${href}`;
      return `<img src="${url}" alt="${alt}" loading="lazy">`;
    })
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const flushList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
  const flushTable = () => {
    if (!tableRows.length) return;
    const [head, ...body] = tableRows;
    html += `<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    tableRows = [];
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.startsWith("```")) {
      flushList(); flushTable();
      if (inCode) { html += `</code></pre>`; inCode = false; }
      else { html += `<pre data-lang="${esc(line.slice(3).trim())}"><code>`; inCode = true; }
      continue;
    }
    if (inCode) { html += line + "\n"; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue; // 分隔行
      tableRows.push(cells);
      continue;
    }
    flushTable();
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) { flushList(); html += `<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`; continue; }
    if (/^\s*[-*]\s+/.test(line)) {
      if (listType !== "ul") { flushList(); html += "<ul>"; listType = "ul"; }
      html += `<li>${inline(line.replace(/^\s*[-*]\s+/, ""))}</li>`; continue;
    }
    if (/^\s*\d+[.、]\s+/.test(line)) {
      if (listType !== "ol") { flushList(); html += "<ol>"; listType = "ol"; }
      html += `<li>${inline(line.replace(/^\s*\d+[.、]\s+/, ""))}</li>`; continue;
    }
    if (line === "---" || line === "***") { flushList(); html += "<hr>"; continue; }
    if (!line.trim()) { flushList(); continue; }
    html += `<p>${inline(line)}</p>`;
  }
  flushList(); flushTable();
  if (inCode) html += "</code></pre>";
  return html;
}

/** 文档中心:左侧文档清单,右侧渲染正文;纯只读,viewer 可用。 */
export function render(root) {
  root.innerHTML = `<div class="docs-center">
    <aside class="dc-list" aria-label="文档清单"><p class="muted">正在读取…</p></aside>
    <article class="dc-body"><div class="dc-empty muted">${icon("copy", 28)}<h3>选择左侧文档</h3><p>项目技术与使用说明都在这里,支持 Markdown 与配图。</p></div></article></div>`;
  const list = $(".dc-list", root), body = $(".dc-body", root);
  let dead = false;
  const request = async (url) => {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`加载失败（HTTP ${r.status}）`);
    return r.text();
  };
  const open = async (name) => {
    body.innerHTML = `<p class="muted">正在加载…</p>`;
    try {
      const text = await request(`/api/project-docs/${encodeURIComponent(name)}`);
      if (dead) return;
      body.innerHTML = `<div class="dc-article">${mdToHtml(text)}</div>`;
      body.scrollTop = 0;
      list.querySelectorAll("[data-doc]").forEach((a) =>
        a.classList.toggle("on", a.dataset.doc === name));
    } catch (e) { if (!dead) body.innerHTML = `<p class="qt-error" role="alert">${esc(e.message)}</p>`; }
  };
  (async () => {
    try {
      const docs = JSON.parse(await request("/api/project-docs"));
      if (dead) return;
      list.innerHTML = docs.length
        ? docs.map((d) => `<a href="#/docscenter?doc=${encodeURIComponent(d.name)}" data-doc="${esc(d.name)}">${esc(d.title)}<span class="dc-size muted num">${(d.size / 1024).toFixed(0)} KB</span></a>`).join("")
        : `<p class="muted">没有找到文档。</p>`;
      const wanted = new URLSearchParams(location.hash.split("?")[1] || "").get("doc");
      open(wanted && docs.some((d) => d.name === wanted) ? wanted : docs[0]?.name).catch(() => {});
    } catch (e) { if (!dead) list.innerHTML = `<p class="qt-error" role="alert">${esc(e.message)}</p>`; }
  })();
  return () => { dead = true; };
}
