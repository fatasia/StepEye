import { esc } from '../util.js';
import { openDrawer } from '../ui.js';

const names = {min:'量程起点',max:'量程终点',tip:'针尖',center:'中心'};
const colors = {min:'#61d9a3',max:'#bf9bff',tip:'#ffc761',center:'#68c9ff'};
const reasons = {
  no_detection:'未定位到表盘，请上传清晰的单个正视表盘。',
  multiple_gauges:'发现多个表盘，请先裁切单个表盘后再试。',
  uncalibrated:'已定位；未提供量程、单位与旋向，不生成读数。',
  invalid_keypoints:'关键点无效，需人工复核。',
  degenerate_keypoint_radius:'关键点距离过近，无法计算角度。',
  degenerate_scale_span:'刻度跨度不足或接近整圆，无法可靠读数。',
  needle_outside_configured_sweep:'针尖超出配置的刻度范围，请检查旋向与端点。'
};
const number = value => Number.isFinite(value) ? value.toLocaleString('zh-CN',{maximumFractionDigits:3}) : '—';

export function openGaugeTrial({canWrite} = {}) {
  if (!canWrite) return;
  let closed = false;
  let busy = false;
  let available = false;
  let image = null;
  let objectURL = null;
  let generation = 0;
  const controller = new AbortController();
  const drawer = openDrawer({title:'指针表盘试测',subtitle:'单张表图 → 定位端点与指针 → 可选量程读数',
    onClose:()=>{
      closed = true;
      generation++;
      controller.abort();
      if (objectURL) URL.revokeObjectURL(objectURL);
    },html:`<style>.gauge-form{font-size:13px;line-height:1.7}.gauge-form label{display:block;margin:14px 0}.gauge-form input:not([type=checkbox]),.gauge-form select{display:block;width:100%;box-sizing:border-box;margin-top:6px;padding:9px;background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);border-radius:6px;font:inherit}.gauge-form input::file-selector-button{background:var(--panel,#172027);color:inherit;border:1px solid var(--border,#34434b);padding:5px 9px;border-radius:4px;margin-right:10px}.gauge-form canvas{display:block;width:100%;height:auto;margin:18px 0;border-radius:7px;background:#0c1217}.gauge-form [role=alert]{color:#ef958b}.gauge-form fieldset{border:0;margin:0;padding:0}.gauge-form .gauge-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.gauge-form .gauge-result{margin:18px 0}.gauge-form .gauge-reading{font-size:28px;font-weight:650}.gauge-form details{margin:18px 0}</style>
      <form class="gauge-form"><p data-gauge-capability role="status">正在检查本地模型…</p>
      <label>表盘图片<input type="file" name="file" accept="image/png,image/jpeg,image/webp" required></label>
      <details><summary>量程与旋向（可选）</summary><label><input name="calibrated" type="checkbox"> 使用我确认的线性量程计算读数</label>
      <fieldset name="range" disabled><div class="gauge-grid"><label>量程下限<input name="minimum" type="number" step="any"></label><label>量程上限<input name="maximum" type="number" step="any"></label></div>
      <label>单位<input name="unit" maxlength="32" placeholder="例如 MPa"></label><label>从下限到上限的旋向<select name="direction"><option value="">请选择</option><option value="clockwise">顺时针</option><option value="counterclockwise">逆时针</option></select></label></fieldset>
      <p>仅适用于线性圆弧刻度。量程由你填写，模型不识别表面数字，也不自动修正透视。</p></details>
      <button class="btn btn-primary" type="submit" disabled>定位并试测</button><p data-gauge-error role="alert"></p>
      <canvas hidden aria-label="表盘关键点与指针叠加"></canvas><section class="gauge-result" data-gauge-result aria-live="polite"></section>
      <p>仅单图评估，结果需复核；不写设备合格结论，不触发报警。模型许可与来源图片使用条件仍需审核。</p></form>`});
  const form = drawer.body.querySelector('form');
  const canvas = form.querySelector('canvas');
  const error = form.querySelector('[data-gauge-error]');
  const result = form.querySelector('[data-gauge-result]');
  const submit = form.querySelector('[type=submit]');
  const capability = form.querySelector('[data-gauge-capability]');
  function draw(detections = []) {
    if (!image) return;
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    canvas.hidden = false;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(image,0,0);
    const scale = Math.max(canvas.width/600,1);
    ctx.lineWidth = 2*scale;
    ctx.font = `${13*scale}px sans-serif`;
    for (const detection of detections) {
      const points = detection.keypoints || [];
      const center = points.find(p=>p.name === 'center');
      const tip = points.find(p=>p.name === 'tip');
      if (center && tip) {
        ctx.strokeStyle = colors.tip;
        ctx.beginPath();ctx.moveTo(center.x_norm*canvas.width,center.y_norm*canvas.height);
        ctx.lineTo(tip.x_norm*canvas.width,tip.y_norm*canvas.height);ctx.stroke();
      }
      for (const point of points) {
        const x = point.x_norm*canvas.width;
        const y = point.y_norm*canvas.height;
        ctx.fillStyle = colors[point.name] || '#fff';
        ctx.beginPath();ctx.arc(x,y,4*scale,0,Math.PI*2);ctx.fill();
        ctx.fillText(names[point.name] || point.name,Math.min(x+7*scale,canvas.width-70*scale),Math.max(y-8*scale,16*scale));
      }
    }
  }
  function clearResult() {
    result.textContent = '';
    error.textContent = '';
    draw();
  }
  form.elements.calibrated.onchange = ()=>{
    form.elements.range.disabled = !form.elements.calibrated.checked;
    clearResult();
  };
  for (const name of ['minimum','maximum','unit','direction']) form.elements[name].oninput = clearResult;
  form.elements.file.onchange = async ()=>{
    const current = ++generation;
    image = null;
    canvas.hidden = true;
    result.textContent = '';
    error.textContent = '';
    submit.disabled = true;
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null;
    const file = form.elements.file.files[0];
    if (!file) return;
    if (file.size > 20*1024*1024) {error.textContent='图片不能超过 20 MB。';return;}
    objectURL = URL.createObjectURL(file);
    const candidate = new Image();
    candidate.src = objectURL;
    try {
      await candidate.decode();
      // A replaced file or closed drawer must not revive an old preview.
      if (closed || current !== generation) return;
      image = candidate;
      draw();
      submit.disabled = !available;
    } catch {
      if (!closed && current === generation) error.textContent='无法读取图片，请使用 PNG、JPEG 或 WebP。';
    }
  };
  form.onsubmit = async event=>{
    event.preventDefault();
    if (busy || !available || !image) return;
    const data = new FormData();
    data.append('file',form.elements.file.files[0]);
    if (form.elements.calibrated.checked) {
      const min = form.elements.minimum.value;
      const max = form.elements.maximum.value;
      const unit = form.elements.unit.value.trim();
      const direction = form.elements.direction.value;
      if (min === '' || max === '' || !Number.isFinite(+min) || !Number.isFinite(+max) || +max <= +min || !unit || !direction) {
        error.textContent='请填写有效上下限、单位和旋向；上限须大于下限。';
        return;
      }
      data.append('calibration',JSON.stringify({min_value:+min,max_value:+max,unit,direction}));
    }
    busy = true;
    clearResult();
    result.textContent = '正在按需启动模型并定位…';
    form.querySelectorAll('input,select,button').forEach(control=>control.disabled=true);
    try {
      const response = await fetch('/api/gauge/trial',{method:'POST',body:data,signal:controller.signal});
      const report = await response.json();
      if (!response.ok) throw new Error(typeof report.detail === 'string' ? report.detail : '输入格式不符合要求。');
      if (closed) return;
      draw(report.detections || []);
      const text = report.status === 'reading' ? '线性量程估计 · 待复核'
        : reasons[report.reason] || reasons[report.status] || '当前无法计算可靠读数。';
      result.innerHTML = `<h3>${esc(text)}</h3>${report.status === 'reading' ? `<div class="gauge-reading">${number(report.reading)} ${esc(report.unit)}</div>` : ''}
        <p>表盘 ${report.detections?.length || 0} 个 · 请求耗时 ${number(report.elapsed_ms)} ms</p>
        <p>绿色：起点 · 紫色：终点 · 黄色：针尖 · 蓝色：中心</p>
        <details><summary>本次模型与定位详情</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(report,null,2))}</pre></details>`;
    } catch (exception) {
      if (!closed && exception.name !== 'AbortError') {error.textContent=exception.message;result.textContent='';}
    } finally {
      busy = false;
      if (!closed) {
        form.querySelectorAll('input,select,button').forEach(control=>control.disabled=false);
        form.elements.range.disabled = !form.elements.calibrated.checked;
        submit.disabled = !available || !image;
      }
    }
  };
  fetch('/api/gauge/capabilities',{signal:controller.signal}).then(async response=>{
    const info = await response.json();
    if (!response.ok) throw new Error('无法读取模型状态。');
    if (closed) return;
    available = info.available === true;
    capability.textContent = available ? '本地模型可用 · 未填写量程时只定位' : '本地表盘模型当前不可用，请检查模型资产与运行依赖。';
    submit.disabled = !available || !image;
  }).catch(exception=>{if (!closed && exception.name !== 'AbortError') capability.textContent=exception.message;});
  return drawer;
}
