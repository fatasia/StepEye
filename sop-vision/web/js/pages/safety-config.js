import { openDrawer } from "../ui.js";
import { esc } from "../util.js";
import { mountWarehouseRules } from "./warehouse-rules.js";

const number = (name, label, value, min, max, step = "any") =>
  `<label>${label}<input name="${name}" type="number" value="${Number(value)}" min="${min}" max="${max}" step="${step}" required></label>`;

export function openSafetySettings(stationId) {
  let dead = false;
  let data = null;
  let calibrations = [];
  let writable = false;
  const controller = new AbortController();
  if (!document.querySelector("[data-safety-config-css]")) {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "/css/safety-config.css";
    css.dataset.safetyConfigCss = "";
    document.head.append(css);
  }
  const drawer = openDrawer({
    title: "工位安全防护",
    subtitle: esc(stationId),
    html: "<p>正在读取配置与权限…</p>",
    onClose: () => {
      dead = true;
      controller.abort();
    },
  });
  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const value = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new Error(typeof value.detail === "string" ? value.detail : `请求失败（${response.status}）`);
    return value;
  }
  const url = `/api/stations/${encodeURIComponent(stationId)}/safety-config`;
  function paint() {
    const ppe = data.ppe,
      proximity = data.proximity;
    const temperatureIndex = data.thermal.rules.findIndex((rule) => rule.kind === "temperature");
    const temperature = data.thermal.rules[temperatureIndex] || {
      id: "temperature-watch",
      source: "",
      name: "temperature",
      threshold: 60,
      min_s: 1,
      min_samples: 3,
      max_gap_s: 2,
    };
    const choices = calibrations.filter(
      (c) =>
        c.validated &&
        c.plane === "ground" &&
        c.unit === "m" &&
        c.image_size.join(",") === data.image_size.join(","),
    );
    drawer.body.innerHTML = `<form class="safety-config-form"><p class="safety-config-note">保存会重载工位；生产周期进行中不能修改。开启配置不代表检测器或设备已就绪。</p><fieldset ${writable ? "" : "disabled"}><section><header><h3>PPE 穿戴观察</h3><label class="safety-switch"><input name="ppe_enabled" type="checkbox" ${ppe.enabled ? "checked" : ""}>启用</label></header><p>逐人观察安全帽、反光衣与手套。缺失判断需要模型明确负类，不按“未检出”报警。</p>${number("ppe_min_s", "连续确认（秒）", ppe.min_s, 0, 3600)}</section><section><header><h3>温场预警</h3><label class="safety-switch"><input name="thermal_enabled" type="checkbox" ${data.thermal.enabled ? "checked" : ""}>启用</label></header><p>接收带来源、采集时间与单位的设备信号；此处配置温度规则，已有温升/面积规则保持不变。</p><div class="safety-grid"><label>设备信号来源<input name="thermal_source" value="${esc(temperature.source)}" maxlength="96" placeholder="thermal-cam"></label><label>信号名称<input name="thermal_name" value="${esc(temperature.name)}" maxlength="96"></label>${number("thermal_threshold", "温度阈值（°C）", temperature.threshold, -273.15, 4000)}${number("thermal_min_s", "连续确认（秒）", temperature.min_s, 0, 3600)}</div><p>工位允许来源：${esc(data.signal_sources?.join("、") || "未配置限制；仍需遵循信号入口权限")}</p></section><section><header><h3>人车距离与车速</h3><label class="safety-switch"><input name="proximity_enabled" type="checkbox" ${proximity.enabled ? "checked" : ""}>启用</label></header><p>仅使用已验证地面米制标定；标定外、断流或轨迹不稳定时返回未知。</p><label>地面标定版本<select name="calibration"><option value="">选择已验证标定</option>${choices.map((c) => `<option value="${esc(c.id)}" ${c.id === proximity.calibration_id ? "selected" : ""}>${esc(c.scope_id)} · v${Number(c.version)} · ${esc(c.reference)}</option>`).join("")}</select></label><div class="safety-grid">${number("near_m", "接近距离（m）", proximity.near_m, 0.01, 100)}${number("speed_mps", "速度阈值（m/s）", proximity.speed_mps, 0.01, 100)}</div></section><details><summary>高级参数</summary><label class="safety-switch"><input name="helmet_probe_enabled" type="checkbox" ${ppe.helmet_probe?.enabled ? "checked" : ""}>安全帽增强识别</label><p>使用独立安全帽模型异步补充识别，需要同时启用 PPE 穿戴观察；其余模型参数保持原配置。</p><div class="safety-grid">${number("ppe_confidence", "PPE 最小置信度", ppe.min_confidence, 0, 1, 0.01)}${number("thermal_samples", "温场连续样本", temperature.min_samples, 2, 10000, 1)}${number("thermal_gap", "温场最大间隔（秒）", temperature.max_gap_s, 0.02, 600)}${number("proximity_min_s", "人车连续确认（秒）", proximity.min_s, 0, 60)}<label>人员类别（逗号分隔）<input name="person_labels" value="${esc(proximity.person_labels.join(","))}"></label><label>车辆类别（逗号分隔）<input name="vehicle_labels" value="${esc(proximity.vehicle_labels.join(","))}"></label></div><p>PPE 区域及其余高级参数保留原配置；未在这里修改的规则不被覆盖。</p></details></fieldset><p data-safety-message role="status">${writable ? "" : "只读账号，仅查看配置。"}</p><button class="btn btn-primary" type="submit" ${writable ? "" : "hidden"}>保存并生效</button></form>`;
    const warehouse = document.createElement("section");
    drawer.body.append(warehouse);
    mountWarehouseRules(warehouse, stationId, controller.signal);
    const form = drawer.body.querySelector("form");
    form.elements.helmet_probe_enabled.onchange = () => {
      if (form.elements.helmet_probe_enabled.checked) form.elements.ppe_enabled.checked = true;
    };
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (!writable || dead) return;
      const f = form.elements;
      const body = {
        ppe: structuredClone(data.ppe),
        thermal: structuredClone(data.thermal),
        proximity: structuredClone(data.proximity),
      };
      body.ppe.enabled = f.ppe_enabled.checked;
      body.ppe.helmet_probe = { ...body.ppe.helmet_probe, enabled: f.helmet_probe_enabled.checked };
      body.ppe.min_s = Number(f.ppe_min_s.value);
      body.ppe.min_confidence = Number(f.ppe_confidence.value);
      body.thermal.enabled = f.thermal_enabled.checked;
      const feedback = form.querySelector("[data-safety-message]");
      if (body.ppe.helmet_probe.enabled && !body.ppe.enabled) {
        feedback.textContent = "安全帽增强识别需要同时启用 PPE 穿戴观察。";
        return;
      }
      if (body.thermal.enabled || temperatureIndex >= 0) {
        if (!f.thermal_source.value.trim() || !f.thermal_name.value.trim()) {
          feedback.textContent = "请填写温场设备来源与信号名称。";
          return;
        }
        const rule = {
          ...temperature,
          source: f.thermal_source.value.trim(),
          name: f.thermal_name.value.trim(),
          kind: "temperature",
          threshold: Number(f.thermal_threshold.value),
          min_s: Number(f.thermal_min_s.value),
          min_samples: Number(f.thermal_samples.value),
          max_gap_s: Number(f.thermal_gap.value),
        };
        if (temperatureIndex >= 0) body.thermal.rules[temperatureIndex] = rule;
        else {
          while (body.thermal.rules.some((r) => r.id === rule.id)) rule.id += "-new";
          body.thermal.rules.push(rule);
        }
      }
      body.proximity.enabled = f.proximity_enabled.checked;
      body.proximity.near_m = Number(f.near_m.value);
      body.proximity.speed_mps = Number(f.speed_mps.value);
      body.proximity.min_s = Number(f.proximity_min_s.value);
      body.proximity.person_labels = f.person_labels.value
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean);
      body.proximity.vehicle_labels = f.vehicle_labels.value
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean);
      const calibration = choices.find((c) => c.id === f.calibration.value);
      if (body.proximity.enabled && !calibration) {
        feedback.textContent = "启用人车防护前，先选择同尺寸、已验证的地面标定。";
        return;
      }
      if (calibration)
        Object.assign(body.proximity, {
          calibration_id: calibration.id,
          scope_id: calibration.scope_id,
          reference: calibration.reference,
          image_size: calibration.image_size,
        });
      const button = form.querySelector("[type=submit]");
      button.disabled = true;
      feedback.textContent = "正在验证并重载工位…";
      try {
        data = await request(url, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (dead) return;
        paint();
        drawer.body.querySelector("[data-safety-message]").textContent =
          "配置已保存并生效；请检查工位当前观测状态。";
      } catch (error) {
        if (!dead && error.name !== "AbortError") feedback.textContent = error.message;
      } finally {
        if (!dead) button.disabled = false;
      }
    };
  }
  Promise.all([request(url), request("/api/auth/me"), request("/api/calibrations")])
    .then(([cfg, me, versions]) => {
      if (dead) return;
      if (!["viewer", "operator", "admin"].includes(me.role)) throw new Error("无法确认访问权限");
      data = cfg;
      calibrations = versions;
      writable = ["operator", "admin"].includes(me.role);
      paint();
    })
    .catch((error) => {
      if (!dead) drawer.body.textContent = error.message;
    });
  return drawer;
}
