import { openDrawer } from "../ui.js";
import { esc } from "../util.js";

function drawer(title) {
  const controller = new AbortController();
  let dead = false;
  if (!document.querySelector("[data-action-reference-css]")) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/css/action-references.css";
    link.dataset.actionReferenceCss = "";
    document.head.append(link);
  }
  const view = openDrawer({
    title,
    html: "<p>正在读取…</p>",
    onClose: () => {
      dead = true;
      controller.abort();
    },
  });
  const request = async (url, options = {}) => {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（${response.status}）`);
    return data;
  };
  return { ...view, request, alive: () => !dead };
}

function card(record) {
  const roles = { start: "开始", key: "关键", end: "完成" };
  return `<article class="action-reference-card"><h3>${esc(record.step_name || record.step_id)} · v${Number(record.version)}</h3><p>${esc(record.station_id)} / ${esc(record.step_id)} · ${esc(record.timeline_name)}</p><div class="action-reference-frames">${Object.entries(
    roles,
  )
    .map(
      ([role, label]) =>
        `<figure><img loading="lazy" src="/api/action-references/${encodeURIComponent(record.id)}/frames/${role}" alt="${label}参考帧"><figcaption>${label} · 请求 ${Number(record.frames[role].requested_time_s).toFixed(2)}s</figcaption></figure>`,
    )
    .join(
      "",
    )}</div><details><summary>来源与版本</summary><p>视频 SHA256：${esc(record.video_sha256)}</p><p>规则指纹：${esc(record.rules_hash)}</p>${Object.entries(
    roles,
  )
    .map(
      ([role, label]) =>
        `<p>${label}：解码时间 ${record.frames[role].decoder_timestamp_s === null ? "不可用" : Number(record.frames[role].decoder_timestamp_s).toFixed(3) + "s"}，帧号 ${record.frames[role].decoder_frame_index ?? "不可用"}</p>`,
    )
    .join("")}<p>参考包 ${esc(record.id)} · ${esc(record.created)}</p></details></article>`;
}

export function openReferenceGallery(stationId) {
  const view = drawer("SOP 动作参考卡");
  view
    .request(`/api/stations/${encodeURIComponent(stationId)}/action-references`)
    .then((data) => {
      if (!view.alive()) return;
      view.body.innerHTML = `<div class="action-reference-content"><p>供作业说明与人工核对，不代表在线动作识别；每张卡保留创建时的规则指纹，不会更新生产规则。</p>${data.items.slice(0, 30).map(card).join("") || "<p>暂无参考卡。先在时间轴中保存区间，再选择“动作卡”关联步骤。</p>"}${data.items.length > 30 ? "<p>仅显示最近30个参考包。</p>" : ""}</div>`;
    })
    .catch((error) => {
      if (view.alive()) view.body.textContent = error.message;
    });
  return view;
}

export function openReferenceCreator(timelineId, intervalIndex) {
  const view = drawer("保存 SOP 动作参考卡");
  let revision = 0;
  Promise.all([
    view.request("/api/auth/me"),
    view.request(`/api/timelines/${encodeURIComponent(timelineId)}`),
    view.request("/api/stations"),
  ])
    .then(async ([me, timeline, stations]) => {
      if (!view.alive()) return;
      if (!["operator", "admin"].includes(me.role)) throw new Error("创建参考包需要操作员权限。");
      const interval = timeline.intervals[intervalIndex];
      if (!interval) throw new Error("请先“校验并抽帧”保存此区间，再创建动作卡。");
      view.body.innerHTML = `<form class="action-reference-form"><p>使用已保存区间 ${Number(interval.start).toFixed(2)}–${Number(interval.end).toFixed(2)} 秒；未保存的表格修改不会进入参考包。</p><label>关联工位<select name="station" required><option value="">选择工位</option>${stations.map((s) => `<option value="${esc(s.meta.id)}">${esc(s.meta.name || s.meta.id)}</option>`).join("")}</select></label><label>关联 SOP 步骤<select name="step" required disabled><option value="">先选择工位</option></select></label><label>关键时刻（原视频秒）<input name="key_time" type="number" step="any" min="${Number(interval.start)}" max="${Number(interval.end)}" value="${(Number(interval.start) + Number(interval.end)) / 2}" required></label><p>自动保留开始、关键、完成三帧及文件SHA。区间结束位于视频尾部时使用最后一帧，并记录实际取帧时间。</p><p data-reference-status role="status"></p><button class="btn btn-primary" type="submit" disabled>保存不可变参考包</button></form><div data-reference-result></div>`;
      const form = view.body.querySelector("form");
      const status = form.querySelector("[data-reference-status]");
      const button = form.querySelector("[type=submit]");
      form.elements.station.onchange = async () => {
        const current = ++revision;
        form.elements.step.disabled = button.disabled = true;
        if (!form.elements.station.value) return;
        try {
          const config = await view.request(
            `/api/stations/${encodeURIComponent(form.elements.station.value)}/config`,
          );
          if (!view.alive() || current !== revision) return;
          form.elements.step.innerHTML = config.steps
            .map(
              (step) =>
                `<option value="${esc(step.id)}">${esc(step.name || step.id)} (${esc(step.id)})</option>`,
            )
            .join("");
          if (config.steps.some((step) => step.id === interval.step))
            form.elements.step.value = interval.step;
          form.elements.step.disabled = button.disabled = config.steps.length === 0;
          status.textContent = config.steps.length ? "" : "该工位没有 SOP 步骤。";
        } catch (error) {
          if (view.alive()) status.textContent = error.message;
        }
      };
      form.onsubmit = async (event) => {
        event.preventDefault();
        const key = Number(form.elements.key_time.value);
        if (!(key > interval.start && key < interval.end)) {
          status.textContent = "关键时刻需严格位于区间内部。";
          return;
        }
        button.disabled = true;
        status.textContent = "正在从原视频提取三帧并核对来源…";
        try {
          const result = await view.request(
            `/api/timelines/${encodeURIComponent(timelineId)}/action-references`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                interval_index: intervalIndex,
                station_id: form.elements.station.value,
                step_id: form.elements.step.value,
                key_time_s: key,
              }),
            },
          );
          if (!view.alive()) return;
          status.textContent = "参考包已保存；未修改生产规则。可在工位配置中查看动作卡。";
          view.body.querySelector("[data-reference-result]").innerHTML = card(result);
        } catch (error) {
          if (view.alive()) status.textContent = error.message;
        } finally {
          if (view.alive()) button.disabled = false;
        }
      };
    })
    .catch((error) => {
      if (view.alive()) view.body.textContent = error.message;
    });
  return view;
}
