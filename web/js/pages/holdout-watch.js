import { esc } from "../util.js";
const states = {observed_drop: "精度观测下降", insufficient_data: "样本不足", no_observed_drop: "未观察到超阈值下降"};

/** New reviewed reports trigger checks server-side; opening this panel never polls. */
export function mountAccuracyWatch(host, record, {canWrite}) {
  host.innerHTML = `<details data-aw-section><summary>后续评测监测</summary>
    <p data-aw-state class="muted">正在读取…</p>
    <div class="he-actions" ${canWrite ? "" : "hidden"}><button class="btn" data-aw-arm>以此报告为基准</button><button class="btn" data-aw-stop hidden>停止监测</button></div>
    <p data-aw-error role="alert" class="he-error"></p></details>`;
  const find = key => host.querySelector(`[data-aw-${key}]`);
  let busy = false;
  const endpoint = `/api/evaluations/holdout/${encodeURIComponent(record.id)}/watch`;
  async function request(options) {
    const response = await fetch(endpoint, options), value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "监测设置失败");
    return value;
  }
  async function refresh() {
    const value = await request();
    if (!host.isConnected) return;
    const config = value.config, result = value.observation;
    find("state").innerHTML = `${config?.enabled ? `已启用 · 基准 ${esc(config.baseline_id)}。同项目、同模型及工作点的新评测入库后自动比较。` : "未启用。设定基准后，新评测自动检查；不增加推理或后台线程。"}
      ${result ? `<br><strong>${esc(states[result.status])}</strong> · 当前 ${result.current_frames} 图 / 基准 ${result.baseline_frames} 图；本次比较基准 ${esc(result.baseline_id)}，结果随完整报告导出。` : ""}
      <br>默认至少 20 图及每类 20 个有效样本，下降 10 个百分点提示；不自动回滚。`;
    find("stop").hidden = !config?.enabled;
    find("arm").textContent = config?.enabled && config.baseline_id === record.id ? "当前基准" : "以此报告为基准";
    find("arm").disabled = !!(config?.enabled && config.baseline_id === record.id);
  }
  async function change(enabled) {
    if (busy) return;
    busy = true;find("arm").disabled = true;find("stop").disabled = true;find("error").textContent = "";
    try { await request({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled})}); await refresh(); }
    catch (error) { if (host.isConnected) {find("error").textContent = error.message;find("arm").disabled = false;} }
    finally { busy = false;if (host.isConnected) find("stop").disabled = false; }
  }
  find("arm").onclick = () => change(true);
  find("stop").onclick = () => change(false);
  refresh().catch(error => {if(host.isConnected) find("error").textContent = error.message;});
}
