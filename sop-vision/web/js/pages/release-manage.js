import { mountReleaseMonitor } from "./release-monitor-ui.js";
import { openDrawer, toast } from "../ui.js";
import { $, esc, fmtTime } from "../util.js";

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（HTTP ${response.status}）`);
  return body;
}

const STATUS = {
  candidate: { text: "待审批", tone: "warning" },
  approved: { text: "已审批", tone: "info" },
  active: { text: "已激活", tone: "success" },
  rolled_back: { text: "已回滚", tone: "muted" },
  superseded: { text: "已替代", tone: "muted" },
};

const short = (sha) => (sha ? String(sha).slice(0, 10) + "…" : "—");

/** 模型发布管理：评测报告 → 候选 → 审批 → 激活到场景 → 可回滚，一屏完成。
 * 运行监控由管理员显式启用，精度仍以独立评测为准。 */
export function openReleaseManagement({ canWrite }) {
  let closed = false, reports = [], isAdmin = false;
  const drawer = openDrawer({
    title: "模型发布管理",
    subtitle: "评测报告 → 候选 → 审批 → 激活到场景 → 回滚",
    html: `<style>.rel-man label{display:block;margin:14px 0 8px}.rel-man select,.rel-man input{max-width:100%;width:100%;box-sizing:border-box;padding:9px;background:transparent;color:inherit;border:1px solid #364348;border-radius:5px}.rel-man table{width:100%;max-width:100%;table-layout:fixed;border-collapse:collapse;font-size:12px;margin:10px 0}.rel-man th,.rel-man td{border-bottom:1px solid #2a3438;padding:7px 6px;text-align:left;vertical-align:top;overflow-wrap:anywhere}.rel-man .rm-error{color:#ff9890}.rel-man .rm-actions{display:flex;gap:8px;flex-wrap:wrap}.rel-man p{line-height:1.7}</style>
      <div class="rel-man">
        <p>发布是独立动作：评测报告只证明"这份 SHA 在这份冻结数据上的表现"，审批人确认后才可激活到场景。评测报告一旦进入发布流程不可重复使用。运行自动回滚默认关闭，仅监测运行错误与成功帧，不监测识别精度。</p>
        ${canWrite ? `<details open><summary>① 从固定评测报告创建候选</summary>
          <label>评测报告（HE-）<select data-rm-report></select></label>
          <div class="rm-actions"><button class="btn" data-rm-create ${canWrite ? "" : "disabled"}>创建候选</button></div>
        </details>` : ""}
        <p data-rm-error class="rm-error" role="alert"></p>
        <details open><summary>发布记录</summary><div data-rm-list>正在读取…</div></details>
      </div>`,
    onClose: () => { closed = true; },
  });
  const body = drawer.body, error = $("[data-rm-error]", body);
  const showError = (e) => { if (!closed) error.textContent = e?.message || ""; };

  function row(r) {
    const st = STATUS[r.status] || { text: r.status, tone: "muted" };
    const actions = [];
    if (canWrite && r.status === "candidate") actions.push(`<button class="btn btn-sm" data-rm-approve="${r.id}">审批</button>`);
    if (canWrite && r.status === "approved") actions.push(`<span class="inline-flex" style="gap:6px"><input data-rm-scene="${r.id}" placeholder="场景 ID" style="width:110px;padding:4px 8px"><button class="btn btn-sm" data-rm-activate="${r.id}">激活</button></span>`);
    if (canWrite && r.status === "active") actions.push(`<button class="btn btn-sm" data-rm-rollback="${r.id}">回滚</button>`);
    return `<tr><td><span class="badge tone-${st.tone}">${st.text}</span><br><span class="mono faint">${esc(r.id)}</span></td>
      <td>${esc(r.engine_id)}<br><span class="mono faint" title="${esc(r.model_sha256)}">${short(r.model_sha256)}</span></td>
      <td class="mono">${esc(String(r.evaluation_id || ""))}</td>
      <td>${esc(r.scene || "—")}${r.replaced_release_id ? `<br><span class="faint">替代 ${short(r.replaced_release_id)}</span>` : ""}<br><span class="faint">${fmtTime(r.created_at * 1000)} · ${esc(r.created_by || "")}</span></td>
      <td><div class="rm-actions">${actions.join("") || '<span class="faint">—</span>'}</div>${r.status === "active" ? `<div data-rm-monitor-host="${esc(r.id)}"></div>` : ""}</td></tr>`;
  }

  async function load() {
    showError("");
    try {
      const [listed, evals] = await Promise.all([
        request("/api/model-releases"),
        canWrite ? request("/api/evaluations/holdout") : Promise.resolve({ items: [] }),
      ]);
      if (closed) return;
      reports = evals.items || [];
      const pick = $("[data-rm-report]", body);
      if (pick) {
        pick.innerHTML = reports.length
          ? reports.map((r) =>
              `<option value="${esc(r.id)}">${esc(r.engine_name || r.engine_id)} · ${esc((r.dataset_version_id || "").slice(0, 14))}… · ${fmtTime(r.created_at * 1000)}</option>`).join("")
          : `<option value="">还没有固定评测报告——先跑一次固定模型评测</option>`;
      }
      $("[data-rm-list]", body).innerHTML = listed.items.length
        ? `<table><colgroup><col style="width:18%"><col style="width:20%"><col style="width:19%"><col style="width:21%"><col style="width:22%"></colgroup><thead><tr><th>状态</th><th>引擎 / SHA</th><th>评测</th><th>场景 / 时间</th><th>操作</th></tr></thead><tbody>${listed.items.map(row).join("")}</tbody></table>`
        : "还没有发布记录——从上方评测报告创建候选。";
      bindActions();
      for (const release of listed.items.filter(r=>r.status === "active")) {
        const host = Array.from(body.querySelectorAll("[data-rm-monitor-host]")).find(node=>node.dataset.rmMonitorHost===release.id);
        mountReleaseMonitor(host, release, {isAdmin,isClosed:()=>closed,onError:showError});
      }
    } catch (e) { showError(e); }
  }

  function bindActions() {
    body.querySelectorAll("[data-rm-approve]").forEach((b) => (b.onclick = () => act(`/api/model-releases/${b.dataset.rmApprove}/approve`, {}, "已审批")));
    body.querySelectorAll("[data-rm-activate]").forEach((b) => (b.onclick = () => {
      const scene = body.querySelector(`[data-rm-scene="${b.dataset.rmActivate}"]`)?.value.trim();
      if (!scene) return showError({ message: "请填场景 ID，如 CELL-A 线体" });
      act(`/api/model-releases/${b.dataset.rmActivate}/activate`, { scene }, `已激活到 ${scene}`);
    }));
    body.querySelectorAll("[data-rm-rollback]").forEach((b) => (b.onclick = () => {
      if (confirm("回滚该场景到上一个已激活版本？")) act(`/api/model-releases/${b.dataset.rmRollback}/rollback`, {}, "已回滚到上一版本");
    }));
  }

  async function act(url, payload, okText) {
    showError("");
    try {
      await request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!closed) { toast(okText); await load(); }
    } catch (e) { showError(e); }
  }

  const createBtn = $("[data-rm-create]", body);
  if (createBtn) createBtn.onclick = async () => {
    const summary = reports.find((r) => r.id === $("[data-rm-report]", body)?.value);
    if (!summary) return showError({ message: "请选择评测报告" });
    showError("");
    try {
      // 列表摘要不含类别表;创建前取一次报告详情
      const report = await request(`/api/evaluations/holdout/${encodeURIComponent(summary.id)}`);
      await request("/api/model-releases", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ engine_id: report.engine_id, model_sha256: report.model_sha256,
                               evaluation_id: report.id, adapter: report.adapter,
                               classes: Object.keys((report.evaluation || {}).classes || {}) }),
      });
      if (!closed) { toast("候选已创建，等待审批"); await load(); }
    } catch (e) { showError(e); }
  };

  request("/api/auth/me").then(me=>{
    if(closed)return;
    if(!["viewer","operator","admin"].includes(me.role))throw new Error("无法确认访问权限");
    isAdmin=me.role === "admin";
    load();
  }).catch(showError);
  return drawer;
}
