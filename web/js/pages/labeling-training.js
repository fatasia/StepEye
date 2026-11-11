import { $, esc, fmtTime } from "../util.js";
import { openDrawer } from "../ui.js";

const STATUS = {
  queued: "排队中",
  running: "训练与验证中",
  candidate: "候选模型已生成",
  failed: "训练失败",
  cancelled: "已取消",
  interrupted: "服务中断，未自动重试",
};
const active = (job) => ["queued", "running"].includes(job.status);

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（HTTP ${response.status}）`);
  return body;
}

/** One project and one base model per drawer; only active tasks trigger polling. */
export function openTraining({ project, engine, canWrite }) {
  let closed = false;
  let timer = null;
  let loading = false;
  let submitting = false;
  let capabilities = null;
  let jobs = [];
  let pendingKey = null;
  const path = `/api/label-projects/${encodeURIComponent(project.id)}/training`;
  const drawer = openDrawer({
    title: "训练与模型增强",
    subtitle: esc(project.name),
    html: `<div class="label-training-intro"><span class="label-kicker">已确认样本 → 固定版本 → 训练候选</span><h3>${esc(engine?.name || "未选择基座模型")}</h3><p>使用当前项目的人工确认数据，从选定模型继续训练。类别和训练参数由平台带入，不需要填写路径。</p><p>至少需要两个独立来源组，并且每个类别在训练和验证集合中都有正样本。合成数据不能代替独立验证。</p></div>
      <div class="label-training-cap" data-training-cap>正在检查训练环境…</div>
      <div class="label-training-actions"><button class="btn btn-primary" data-training-submit ${canWrite ? "" : "hidden"} disabled>一键训练 / 增强</button><button class="btn" data-training-refresh>刷新记录</button></div>
      <p class="label-help">结果先进入候选模型，可试跑和预标注；不会自动替换生产模型。取消、失败或服务中断后不会自动重试。</p>
      <div class="label-training-error" data-training-error role="alert" hidden></div><div data-training-list aria-live="polite"></div>`,
    onClose: () => {
      // The backend owns submitted jobs; closing the drawer only stops UI polling.
      closed = true;
      clearTimeout(timer);
    },
  });
  const showError = (message) => {
    if (closed) return;
    const el = $("[data-training-error]", drawer.body);
    el.hidden = !message;
    el.textContent = message;
  };
  function controls() {
    if (closed) return;
    const start = $("[data-training-submit]", drawer.body);
    start.disabled =
      !canWrite || !engine?.available || !capabilities?.configured || submitting || loading || jobs.some(active);
    start.textContent = submitting
      ? "正在创建数据版本…"
      : pendingKey
        ? "重试提交（同一任务）"
        : "一键训练 / 增强";
    $("[data-training-refresh]", drawer.body).disabled = loading || submitting;
  }
  function paint() {
    const config = $("[data-training-cap]", drawer.body);
    if (!capabilities?.configured)
      config.textContent = capabilities?.reason || "训练环境尚未配置，请联系管理员。";
    else if (!engine?.available) config.textContent = "当前没有可用基座，请先在工作台选择已登记模型。";
    else
      config.textContent = `训练环境已配置 · ${capabilities.device === "cpu" ? "CPU" : `GPU ${capabilities.device}`} · 默认 ${capabilities.defaults?.epochs ?? 30} 轮。提交后还会校验数据和本机训练权重。`;
    $("[data-training-list]", drawer.body).innerHTML = jobs.length
      ? jobs
          .map(
            (job) => `<article class="label-training-job">
      <header><b>${STATUS[job.status] || esc(job.status)}</b><span>${job.created_at ? fmtTime(job.created_at * 1000) : ""}</span></header>
      <dl><dt>任务</dt><dd>${esc(job.id)}</dd><dt>数据版本</dt><dd>${esc(job.version_id || "待生成")}</dd></dl>
      ${job.error ? `<p class="label-training-error">${esc(job.error)}</p>` : ""}
      ${active(job) ? `<p class="label-help">实际训练进度由工作进程执行；此处不估算虚假百分比。</p>${canWrite ? `<button class="label-link" data-training-cancel="${esc(job.id)}">取消任务</button>` : ""}` : ""}
      ${job.status === "candidate" ? `<p class="label-help">已生成候选，仍需独立评测和目标工位验证。</p><a class="btn" href="${project.scene_id ? `#/applications/${encodeURIComponent(project.scene_id)}?candidate=${encodeURIComponent("trained-"+job.id)}` : "#/models"}">${project.scene_id ? "回到应用验证与发布" : "到模型实验室试跑候选"}</a><details><summary class="label-link">验证指标与模型指纹</summary><pre class="label-report-json">${esc(JSON.stringify({ metrics: job.metrics || {}, model_sha256: job.model_sha256, dataset_sha256: job.dataset_sha256 }, null, 2))}</pre></details>` : ""}
      </article>`,
          )
          .join("")
      : '<p class="label-help">本项目还没有训练任务。先完成人工确认，再创建第一个候选。</p>';
    drawer.body.querySelectorAll("[data-training-cancel]").forEach((button) => {
      button.onclick = async () => {
        if (!window.confirm("取消这个训练任务？已运行部分不会自动恢复。")) return;
        button.disabled = true;
        try {
          await request(`/api/training/jobs/${encodeURIComponent(button.dataset.trainingCancel)}/cancel`, {
            method: "POST",
          });
          await load();
        } catch (error) {
          showError(error.message);
          button.disabled = false;
        }
      };
    });
    controls();
  }
  async function load() {
    if (closed || loading) return;
    clearTimeout(timer);
    loading = true;
    controls();
    try {
      const [config, rows] = await Promise.all([request("/api/training/capabilities"), request(path)]);
      if (closed) return;
      capabilities = config;
      jobs = rows;
      paint();
      // No idle interval: retire polling as soon as all jobs reach terminal states.
      if (jobs.some(active)) timer = setTimeout(load, 1800);
    } catch (error) {
      showError(error.message);
    } finally {
      loading = false;
      controls();
    }
  }
  $("[data-training-refresh]", drawer.body).onclick = load;
  $("[data-training-submit]", drawer.body).onclick = async () => {
    if (submitting || loading || !canWrite || !engine?.available || !capabilities?.configured) return;
    pendingKey ||= crypto.randomUUID();
    submitting = true;
    showError("");
    controls();
    try {
      await request(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ engine_id: engine.id, idempotency_key: pendingKey }),
      });
      pendingKey = null;
      await load();
    } catch (error) {
      // Keep the key after ambiguous network failures so retry cannot spend twice.
      showError(error.message);
    } finally {
      submitting = false;
      controls();
    }
  };
  load();
  return drawer;
}
