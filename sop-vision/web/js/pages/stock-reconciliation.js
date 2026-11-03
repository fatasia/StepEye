import { openDrawer, toast } from "../ui.js";
import { $, esc, fmtTime } from "../util.js";

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（HTTP ${response.status}）`);
  return body;
}

const num = (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN"));

function download(value, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 期望库存 JSON 行格式：[{zone,item,count}]；对账观测同结构。 */
function readItems(text) {
  const parsed = JSON.parse(text);
  const rows = Array.isArray(parsed) ? parsed : parsed.items;
  if (!Array.isArray(rows) || !rows.length) throw new Error("JSON 需要是 [{zone,item,count}] 数组");
  return rows.map((r) => ({ zone: String(r.zone ?? ""), item: String(r.item ?? ""), count: Number(r.count ?? -1) }))
    .map((r) => {
      if (!r.zone.trim() || !r.item.trim()) throw new Error("每行都需要 zone 和 item");
      if (!Number.isInteger(r.count) || r.count < 0) throw new Error(`${r.zone}/${r.item} 的 count 须为非负整数`);
      return r;
    });
}

/** 盘点账实核对：导入期望值 → 填当前可见数 → 对账看差异，三步。跨机位去重未实现。 */
export function openStockReconciliation({ canWrite }) {
  let closed = false, last = null;
  const drawer = openDrawer({
    title: "盘点对账",
    subtitle: "导入期望库存 → 录入当前可见数 → 对账差异",
    html: `<style>.stock-recon label{display:block;margin:14px 0 8px}.stock-recon input,.stock-recon select,.stock-recon textarea{max-width:100%;width:100%;box-sizing:border-box;padding:9px;background:transparent;color:inherit;border:1px solid #364348;border-radius:5px}.stock-recon textarea{font-family:var(--font-mono,monospace);font-size:12px}.stock-recon table{width:100%;border-collapse:collapse;font-size:12px;margin:10px 0}.stock-recon th,.stock-recon td{border-bottom:1px solid #2a3438;padding:6px 8px;text-align:left}.stock-recon .sr-error{color:#ff9890}.stock-recon .sr-actions{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.stock-recon .diff-short{color:#ff9890}.stock-recon .diff-extra{color:#ffd47f}.stock-recon p{line-height:1.7}</style>
      <div class="stock-recon">
        <label>工位 / 场景<input data-sr-station placeholder="如 WMS-A" ${canWrite ? "" : "disabled"}></label>
        <details ${canWrite ? "open" : ""}><summary>① 导入期望库存（JSON：[{"zone","item","count"}]）</summary>
          <label>期望值文件<input data-sr-file type="file" accept=".json" ${canWrite ? "" : "disabled"}></label>
          <div class="sr-actions"><button class="btn" data-sr-import ${canWrite ? "" : "disabled"}>导入期望值（整批替换并留版本）</button></div>
        </details>
        <details ${canWrite ? "open" : ""}><summary>② 录入当前可见数（JSON 同结构，来自单机位计数快照）</summary>
          <label>观测值<input data-sr-observed placeholder='[{"zone":"A","item":"货箱","count":12}]' ${canWrite ? "" : "disabled"}></label>
          <div class="sr-actions"><button class="btn btn-primary" data-sr-run ${canWrite ? "" : "disabled"}>对账</button></div>
        </details>
        <p data-sr-error class="sr-error" role="alert"></p>
        <section data-sr-result aria-live="polite">尚未对账。</section>
        <button class="btn" data-sr-export hidden>导出报告 JSON</button>
        <details open><summary>历史对账报告</summary><div data-sr-history>正在读取…</div></details>
      </div>`,
    onClose: () => { closed = true; },
  });
  const body = drawer.body, error = $("[data-sr-error]", body);
  const showError = (e) => { if (!closed) error.textContent = e?.message || ""; };
  const station = () => $("[data-sr-station]", body).value.trim();

  const ROW_LABEL = { missing: ["缺", "diff-short"], surplus: ["多", "diff-extra"], unexpected: ["账外", "diff-extra"] };
  function render(report) {
    last = report;
    const s = report.summary || {};
    const rows = (report.rows || []).filter((r) => r.status !== "match");
    $("[data-sr-result]", body).innerHTML = `
      <h3>${esc(report.station)} · 对账结果</h3>
      <p>${esc(report.note || "")} 一致 ${num(s.match)} · 缺 ${num(s.missing)} · 多 ${num(s.surplus)} · 账外 ${num(s.unexpected)} · 未观测 ${num(s.unobserved)}<span class="muted">（期望版本 ${esc(String(report.expected_version ?? "—"))}）</span></p>
      ${rows.length ? `<table><thead><tr><th>差异</th><th>区域</th><th>物品</th><th>期望</th><th>实际</th></tr></thead><tbody>
        ${rows.map((r) => { const [label, cls] = ROW_LABEL[r.status] || [esc(r.status), ""]; return `<tr class="${cls}"><td>${label}</td><td>${esc(r.zone)}</td><td>${esc(r.item)}</td><td>${num(r.count)}</td><td>${r.observed_count == null ? "未报" : num(r.observed_count)}</td></tr>`; }).join("")}
      </tbody></table>` : "<p>没有差异。</p>"}
      <p class="muted">${esc(report.scope || "单机位可见计数对账；跨机位去重未实现")}，结果仅供盘点复核。</p>`;
    $("[data-sr-export]", body).hidden = false;
  }

  $("[data-sr-import]", body).onclick = async () => {
    const file = $("[data-sr-file]", body).files[0];
    if (!station()) return showError({ message: "请先填工位/场景" });
    if (!file) return showError({ message: "请选择期望库存 JSON 文件" });
    if (file.size > 1024 * 1024) return showError({ message: "文件不能超过 1MB" });
    showError("");
    try {
      const record = await request("/api/stock/expected", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ station: station(), items: readItems(await file.text()) }),
      });
      if (!closed) toast(`期望值已导入为版本 ${record.version}`);
    } catch (e) { showError(e); }
  };

  $("[data-sr-run]", body).onclick = async () => {
    if (!station()) return showError({ message: "请先填工位/场景" });
    showError("");
    try {
      const observed = $("[data-sr-observed]", body).value.trim();
      const payload = { station: station(), observed: observed ? readItems(observed) : [] };
      const report = await request("/api/stock/reconcile", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!closed) { render(report); await loadHistory(); }
    } catch (e) { showError(e); }
  };

  async function loadHistory() {
    try {
      if (!station()) { $("[data-sr-history]", body).textContent = "填入工位/场景后显示该站历史。"; return; }
      const rows = await request(`/api/stock/reconciliations?station=${encodeURIComponent(station())}&limit=20`);
      if (closed) return;
      $("[data-sr-history]", body).innerHTML = rows.reports?.length
        ? rows.reports.map((r) => `<button class="btn" style="display:block;width:100%;text-align:left;margin:8px 0" data-sr-record="${esc(r.id)}">${esc(r.station)} · ${fmtTime(r.created_at * 1000)} · 缺 ${r.summary?.missing ?? "—"} / 多 ${r.summary?.surplus ?? "—"}</button>`).join("")
        : "还没有对账记录。";
      body.querySelectorAll("[data-sr-record]").forEach((button) => {
        button.onclick = async () => {
          try { render(await request(`/api/stock/reconciliations/${encodeURIComponent(button.dataset.srRecord)}`)); }
          catch (e) { showError(e); }
        };
      });
    } catch (e) { showError(e); }
  }

  $("[data-sr-station]", body).onchange = loadHistory;
  $("[data-sr-export]", body).onclick = () => last && download(last, `${last.id || "reconciliation"}.json`);
  loadHistory();
  return drawer;
}
