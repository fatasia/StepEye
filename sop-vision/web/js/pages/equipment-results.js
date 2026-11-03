import { $, esc, fmtTime } from "../util.js";
import { openDrawer, openLightbox } from "../ui.js";

const endpoint = "/api/equipment-results";
const verdicts = { PASS: "通过", NG: "不通过", UNCERTAIN: "待人工确认" };
async function request(url, options = {}) {
  const response = await fetch(url, options), body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（HTTP ${response.status}）`);
  return body;
}
const post = body => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const localNow = () => {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

/** 设备原判与复判并列保存；没有模型时仍可导入、查图和人工复核。 */
export function openEquipmentResults({ canWrite }) {
  let closed = false, selected = null, detailGeneration = 0, listGeneration = 0, offset = 0, total = 0;
  let uploadKey = crypto.randomUUID(), scenarios = [], reviewKey = crypto.randomUUID();
  const machineKeys = new Map(), submitted = new Set(), limit = 25;
  const drawer = openDrawer({ title: "设备质检结果", subtitle: "保留设备原判，单独记录人工与模型复判",
    onClose: () => { closed = true; }, html: `
    <style>.equipment-results{font-size:13px;line-height:1.6}.equipment-results h3{margin:20px 0 10px}.equipment-results label{display:grid;gap:5px;min-width:0}.equipment-results input,.equipment-results select,.equipment-results textarea{width:100%;box-sizing:border-box;min-width:0;background:var(--panel,transparent);color:inherit;border:1px solid var(--border,#58656a);border-radius:6px;padding:9px;font:inherit}.equipment-results option{color:var(--text,#dce6e8);background:var(--panel,#182126)}.equipment-results textarea{resize:vertical}.equipment-results .er-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}.equipment-results .er-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:12px 0}.equipment-results .er-notice{color:var(--muted,#97a6af)}.equipment-results .er-error{color:var(--danger,#e46e68);overflow-wrap:anywhere}.equipment-results .er-row{display:block;width:100%;text-align:left;padding:12px;margin:6px 0;white-space:normal}.equipment-results .er-row[aria-current=true]{border-color:var(--accent,#76c9b5)}.equipment-results .er-row small{display:block;color:var(--muted,#97a6af)}.equipment-results .er-original{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px;border:1px solid var(--border,#58656a);border-radius:8px}.equipment-results .er-original strong{font-size:25px}.equipment-results .er-images{display:grid;gap:12px;margin:16px 0}.equipment-results .er-images button{padding:0;overflow:hidden;border:1px solid var(--border,#58656a);border-radius:7px;background:transparent;cursor:zoom-in}.equipment-results img{display:block;width:100%;max-height:310px;object-fit:contain;background:#111}.equipment-results .er-history{padding:12px 0;border-top:1px solid var(--border,#58656a);overflow-wrap:anywhere}.equipment-results pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}.equipment-results summary{cursor:pointer}.equipment-results [hidden]{display:none!important}@media(max-width:420px){.equipment-results .er-grid{grid-template-columns:1fr}}</style>
    <div class="equipment-results">
      <details data-er-upload ${canWrite ? "open" : "hidden"}><summary>导入设备结果</summary>
        <form data-er-form>
          <div class="er-grid"><label>设备 ID<input name="device_id" required maxlength="96" pattern="[A-Za-z0-9_.-]+" placeholder="AOI-001"></label><label>产品 SN<input name="sn" required maxlength="160" placeholder="产品序列号"></label>
            <label>设备采集时间（本机时区）<input name="observed_at" type="datetime-local" value="${localNow()}" required></label><label>设备原判<select name="result"><option value="NG">NG · 设备判不良</option><option value="OK">OK · 设备判合格</option></select></label></div>
          <label>缺陷类别（逗号分隔，可留空）<input name="defects" placeholder="短路, 缺焊"></label>
          <details><summary>设备类型</summary><label>来源<select name="source_kind"><option>AOI</option><option>AXI</option><option value="line_scan">线扫</option><option value="xray">X-ray</option><option value="ct">CT</option><option value="other">其他</option></select></label></details>
          <label>证据图片<input name="files" type="file" multiple accept="image/png,image/jpeg,image/webp,image/bmp" required></label>
          <p class="er-notice">1–5 张，每张不超过 10MB，总计不超过 25MB。原判和图片一起保存；设备提交不等于人工真值。</p>
          <button class="btn btn-primary" type="submit" data-er-save>保存设备原判</button>
        </form>
      </details>
      <p class="er-error" data-er-error role="alert"></p><p data-er-status role="status"></p>
      <h3>已导入结果</h3><form data-er-filter><div class="er-grid"><label>设备 ID<input name="device_id" placeholder="全部设备"></label><label>产品 SN<input name="sn" placeholder="全部产品"></label></div>
        <div class="er-actions"><label>原判<select name="result"><option value="">全部</option><option>NG</option><option>OK</option></select></label><button class="btn" type="submit">筛选 / 刷新</button><span data-er-count></span></div></form>
      <div data-er-list>正在读取…</div><div class="er-actions"><button class="btn" data-er-prev>上一页</button><button class="btn" data-er-next>下一页</button></div>
      <section data-er-detail aria-live="polite"><p class="er-notice">选择一条记录查看证据和复判。</p></section>
    </div>` });
  const body = drawer.body, form = $("[data-er-form]", body), filter = $("[data-er-filter]", body);
  const error = e => { if (!closed) $("[data-er-error]", body).textContent = e?.message || ""; };
  const status = message => { if (!closed) $("[data-er-status]", body).textContent = message; };
  const busy = async (button, action) => {
    button.disabled = true; error(null);
    try { await action(); } catch (e) { error(e); } finally { if (!closed) button.disabled = false; }
  };
  async function listing() {
    const generation = ++listGeneration, query = new URLSearchParams(new FormData(filter));
    query.set("limit", limit); query.set("offset", offset);
    const data = await request(`${endpoint}?${query}`);
    if (closed || generation !== listGeneration) return;
    total = data.total;
    $("[data-er-count]", body).textContent = `${total} 条`;
    $("[data-er-prev]", body).disabled = offset === 0;
    $("[data-er-next]", body).disabled = offset + limit >= total;
    $("[data-er-list]", body).innerHTML = data.items.length ? data.items.map(r => `<button class="btn er-row" data-er-id="${esc(r.id)}" aria-current="${r.id === selected?.id}"><b>${esc(r.sn)} · ${esc(r.result)}</b><small>${esc(r.device_id)} · ${fmtTime(r.observed_at)}</small></button>`).join("") : "没有匹配的设备结果。";
    body.querySelectorAll("[data-er-id]").forEach(button => button.onclick = () => detail(button.dataset.erId).catch(error));
  }
  async function history(id, generation) {
    const data = await request(`${endpoint}/${encodeURIComponent(id)}/reviews`);
    if (closed || generation !== detailGeneration) return;
    $("[data-er-history]", body).innerHTML = data.items.length ? data.items.map(r => {
      const human = r.producer === "human", task = r.inspection;
      const result = human ? verdicts[r.verdict] : task ? `${task.status} · ${verdicts[task.verdict] || "尚无判定"}` : ({ submitted: "已提交，当前任务状态不可用", failed: "提交失败", dispatch_unknown: "分派结果未知，请先核查任务，勿重复提交" }[r.status] || r.status);
      return `<article class="er-history"><b>${human ? "人工复判" : "模型建议"} · ${esc(result)}</b><br><span>${esc(r.actor)} · ${fmtTime(r.created_at * 1000)}</span>${r.reason || task?.reason ? `<p>${esc(r.reason || task.reason)}</p>` : ""}${r.task_id ? `<small>任务 ${esc(r.task_id)}</small>` : ""}</article>`;
    }).join("") : "尚无复判记录。";
  }
  async function detail(id) {
    const generation = ++detailGeneration, r = await request(`${endpoint}/${encodeURIComponent(id)}`);
    if (closed || generation !== detailGeneration) return;
    selected = r; reviewKey = crypto.randomUUID();
    body.querySelectorAll("[data-er-id]").forEach(b => b.setAttribute("aria-current", String(b.dataset.erId === id)));
    const host = $("[data-er-detail]", body);
    host.innerHTML = `<h3>设备原判与证据</h3><div class="er-original"><div><b>${esc(r.sn)}</b><br>${esc(r.device_id)} · ${esc(r.source_kind)}</div><strong data-er-original>${esc(r.result)}</strong></div>
      <p>采集 ${fmtTime(r.observed_at)}<br>缺陷 ${esc(r.defects.join("、") || "未填写")}</p>
      <div class="er-images">${r.images.map((image, i) => `<button data-er-image="${i}" aria-label="查看证据图片 ${i + 1}"><img src="${endpoint}/${encodeURIComponent(id)}/images/${i}" alt="${esc(image.name)}" loading="lazy"></button>`).join("")}</div>
      <details><summary>导入信息与图片摘要</summary><pre data-er-metadata></pre></details>
      <section ${canWrite ? "" : "hidden"}><h3>人工复判</h3><form data-er-review><label>独立判定<select name="verdict"><option value="UNCERTAIN">待人工确认</option><option value="PASS">通过</option><option value="NG">不通过</option></select></label><label>复核原因<textarea name="reason" required maxlength="2000" rows="2"></textarea></label><div class="er-actions"><button class="btn" data-er-review-save type="submit">保存人工复判</button></div></form>
      <details><summary>使用现有即检场景复判</summary><p class="er-notice">模型输出仅作建议，不覆盖设备原判或人工复判。</p><p data-er-machine-status></p><label data-er-scene-label hidden>即检场景<select data-er-scene></select></label><button class="btn" data-er-recheck hidden>提交模型复判</button></details></section>
      <div class="er-actions"><h3>复判记录</h3><button class="btn" data-er-history-refresh>刷新记录</button></div><div data-er-history>正在读取…</div>`;
    $("[data-er-metadata]", host).textContent = JSON.stringify({ id: r.id, actor: r.actor, received_at: r.received_at, images: r.images }, null, 2);
    host.querySelectorAll("[data-er-image]").forEach(button => button.onclick = () => openLightbox(`${endpoint}/${encodeURIComponent(id)}/images/${button.dataset.erImage}`, `${r.device_id} · ${r.sn}`));
    $("[data-er-history-refresh]", host).onclick = () => history(id, generation).catch(error);
    if (canWrite) {
      const review = $("[data-er-review]", host);
      review.oninput = () => { reviewKey = crypto.randomUUID(); };
      review.onsubmit = e => { e.preventDefault(); busy($("[data-er-review-save]", host), async () => {
        const values = Object.fromEntries(new FormData(review));
        await request(`${endpoint}/${encodeURIComponent(id)}/reviews`, post({ ...values, idempotency_key: reviewKey }));
        if (closed || generation !== detailGeneration) return;
        status("人工复判已保存；设备原判保持不变。"); await history(id, generation);
      }); };
      const scene = $("[data-er-scene]", host), button = $("[data-er-recheck]", host);
      const updateMachine = () => { button.disabled = submitted.has(`${id}:${scene.value}`); };
      scene.innerHTML = scenarios.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
      $("[data-er-scene-label]", host).hidden = !scenarios.length; button.hidden = !scenarios.length;
      $("[data-er-machine-status]", host).textContent = scenarios.length ? "选择已启用的场景，提交后手动刷新记录。" : "当前无可用即检服务或已启用场景。可继续人工复判。";
      scene.onchange = updateMachine; updateMachine();
      button.onclick = () => busy(button, async () => {
        const key = `${id}:${scene.value}`;
        if (!machineKeys.has(key)) machineKeys.set(key, crypto.randomUUID());
        const record = await request(`${endpoint}/${encodeURIComponent(id)}/rechecks`, post({ scenario_id: scene.value, idempotency_key: machineKeys.get(key) }));
        submitted.add(key);
        if (closed || generation !== detailGeneration) return;
        status(record.status === "dispatch_unknown" ? "分派结果未知，请核查任务后再操作。" : "模型复判已提交，不改变设备原判。");
        await history(id, generation);
      }).then(updateMachine);
    }
    await history(id, generation);
  }
  form.oninput = () => { uploadKey = crypto.randomUUID(); };
  form.onsubmit = e => { e.preventDefault(); busy($("[data-er-save]", body), async () => {
    const files = [...form.elements.files.files], values = Object.fromEntries(new FormData(form));
    if (files.length < 1 || files.length > 5 || files.some(f => f.size > 10 * 1024 ** 2) || files.reduce((n, f) => n + f.size, 0) > 25 * 1024 ** 2) throw new Error("请选择 1–5 张图片，每张不超过 10MB，总计不超过 25MB");
    const payload = { device_id: values.device_id.trim(), sn: values.sn.trim(), observed_at: new Date(values.observed_at).toISOString(), result: values.result, source_kind: values.source_kind, defects: [...new Set(values.defects.split(/[,，]/).map(v => v.trim()).filter(Boolean))], idempotency_key: uploadKey };
    const data = new FormData(); data.append("metadata", JSON.stringify(payload)); files.forEach(f => data.append("files", f));
    const response = await request(endpoint, { method: "POST", body: data });
    if (closed) return;
    uploadKey = crypto.randomUUID(); $("[data-er-upload]", body).open = false;
    status(response.created ? "设备原判与图片已保存。" : "相同结果已存在，未重复导入。");
    filter.reset(); offset = 0; await listing(); await detail(response.result.id);
    $("[data-er-detail]", body).scrollIntoView({ block: "start", behavior: "smooth" });
  }); };
  filter.onsubmit = e => { e.preventDefault(); offset = 0; listing().catch(error); };
  $("[data-er-prev]", body).onclick = () => { offset = Math.max(0, offset - limit); listing().catch(error); };
  $("[data-er-next]", body).onclick = () => { if (offset + limit < total) { offset += limit; listing().catch(error); } };
  (async () => {
    if (canWrite) scenarios = await request("/api/inspection/scenarios").then(rows => rows.filter(s => s.enabled)).catch(() => []);
    if (!closed) await listing();
  })().catch(error);
  return drawer;
}
