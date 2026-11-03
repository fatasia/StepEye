import { $, esc } from '../util.js';
import { openDrawer } from '../ui.js';

/** Browser-only image picking; all calibration mathematics stays in the existing API. */
export function openCalibrationEditor({ canWrite = false, scopeId = '', reference = '', onSaved } = {}) {
  let image = null, url = null, closed = false, generation = 0, controller = null;
  let points = [], checks = [], mode = 'points';
  const drawer = openDrawer({ title: '点选平面标定', subtitle: '选图 → 对应坐标 → 独立验证', html: `
    <style>.cal-editor [hidden]{display:none!important}.cal-editor label{display:block;margin:12px 0}.cal-editor input,.cal-editor select{box-sizing:border-box;max-width:100%;padding:8px;color:inherit;background:var(--surface,#172027);border:1px solid var(--border,#364348);border-radius:5px}.cal-editor input[type=number]{width:100%}.cal-editor canvas{display:block;width:100%;height:auto;cursor:crosshair;touch-action:manipulation;border:1px solid var(--border,#364348)}.cal-editor .ce-actions{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.cal-editor table{width:100%;border-collapse:collapse;table-layout:fixed}.cal-editor th,.cal-editor td{padding:5px;text-align:left;border-bottom:1px solid var(--border,#364348)}.cal-editor th:first-child{width:28%}.cal-editor th:last-child{width:42px}.cal-editor small{display:block;color:var(--muted,#98a7ae);margin-top:4px}.cal-editor details{margin:18px 0}.cal-editor [role=alert]{color:#ff9890}.cal-editor output{display:block;line-height:1.6;margin:16px 0}.cal-editor .ce-note{line-height:1.7;color:var(--muted,#a5b2b8)}</style>
    <section class="cal-editor"><p class="ce-note">在同一平面选至少4个分散的标定点，填写已知实际坐标；另选至少2个内部验证点，不重复使用标定点。</p>
      <label>参考图片<input data-ce-file type="file" accept="image/png,image/jpeg,image/webp,image/bmp" ${canWrite ? '' : 'disabled'}></label>
      <label>标定用途 <select data-ce-unit ${canWrite ? '' : 'disabled'}><option value="mm">工件平面 · mm</option><option value="m">地面平面 · m</option></select></label>
      <div class="ce-actions"><button class="btn btn-primary" data-ce-mode="points">① 标定点</button><button class="btn" data-ce-mode="checks">② 独立验证点</button><button class="btn" data-ce-clear disabled>清空点位</button></div>
      <p data-ce-hint>请选择图片，最多10MB / 2000万像素。图片仅在本机浏览器处理。</p>
      <canvas data-ce-canvas hidden aria-label="点击图片添加标定或独立验证点"></canvas>
      <h3 data-ce-heading>标定点 · 0</h3><table><thead><tr><th>编号 / 像素</th><th>实际 X</th><th>实际 Y</th><th>删除</th></tr></thead><tbody data-ce-rows></tbody></table>
      <label>允许误差 <input data-ce-tolerance type="number" value="1" step="any" min="0.000001" max="1000"> <span data-ce-unit-label>mm</span></label>
      <details><summary>来源与参考状态</summary><label>来源范围<input data-ce-scope maxlength="160" value="${esc(scopeId || 'manual-image')}"></label><label>图像参考<input data-ce-reference maxlength="200" value="${esc(reference)}"></label><p class="ce-note">默认按图片内容生成参考指纹。转移至实时相机前，请确认画面来源、位置、分辨率和参考状态一致。</p></details>
      <div class="ce-actions"><button class="btn btn-primary" data-ce-save disabled>验证并保存版本</button></div>
      <p role="alert" data-ce-error></p><output data-ce-result aria-live="polite"></output>
      <p class="ce-note">只适用于该平面和标定覆盖区；不包含镜头畸变校正或现场测量精度认证。</p>
    </section>`, onClose() {
      closed = true; generation++; controller?.abort(); if (url) URL.revokeObjectURL(url);
    } });
  const body = drawer.body, canvas = $('[data-ce-canvas]', body), ctx = canvas.getContext('2d');
  const fileInput = $('[data-ce-file]', body), error = $('[data-ce-error]', body), output = $('[data-ce-result]', body);
  const save = $('[data-ce-save]', body), rows = $('[data-ce-rows]', body), clear = $('[data-ce-clear]', body);
  const scope = $('[data-ce-scope]', body), ref = $('[data-ce-reference]', body), unit = $('[data-ce-unit]', body);
  const tolerance = $('[data-ce-tolerance]', body), hint = $('[data-ce-hint]', body);
  function dirty() { output.textContent = ''; error.textContent = ''; }
  function eligibility() { save.disabled = !canWrite || !image || !!controller || points.length < 4 || checks.length < 2; }
  function draw() {
    if (!image) return;
    ctx.drawImage(image, 0, 0); const radius = Math.max(5, canvas.width / 90);
    ctx.font = `bold ${Math.max(12, radius * 1.7)}px sans-serif`;
    for (const [list, color, prefix] of [[points, '#2bd5c5', 'P'], [checks, '#ffbc66', 'V']]) list.forEach((p, i) => {
      ctx.beginPath(); ctx.arc(...p.pixel, radius, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
      ctx.strokeStyle = '#102027'; ctx.lineWidth = Math.max(2, radius / 4); ctx.stroke();
      ctx.fillText(prefix + (i + 1), p.pixel[0] + radius + 3, p.pixel[1] - radius);
    });
  }
  function render() {
    const list = mode === 'points' ? points : checks;
    $('[data-ce-heading]', body).textContent = `${mode === 'points' ? '标定点' : '独立验证点'} · ${list.length}`;
    rows.innerHTML = list.map((p, i) => `<tr><td>${mode === 'points' ? 'P' : 'V'}${i + 1}<small>${p.pixel.join(', ')}</small></td><td><input aria-label="点${i + 1}实际X" data-axis="0" data-row="${i}" type="number" step="any" value="${esc(p.physical[0])}"></td><td><input aria-label="点${i + 1}实际Y" data-axis="1" data-row="${i}" type="number" step="any" value="${esc(p.physical[1])}"></td><td><button class="btn" data-delete="${i}" aria-label="删除点${i + 1}">×</button></td></tr>`).join('');
    for (const button of body.querySelectorAll('[data-ce-mode]')) button.classList.toggle('btn-primary', button.dataset.ceMode === mode);
    hint.textContent = image ? `标定 ${points.length}/至少4 · 验证 ${checks.length}/至少2 · 当前点击添加${mode === 'points' ? '标定点' : '验证点'}` : '请先选择图片。';
    clear.disabled = !image || !!controller; eligibility(); draw();
  }
  rows.oninput = e => {
    if (controller || e.target.dataset.axis == null) return;
    const list = mode === 'points' ? points : checks;
    list[Number(e.target.dataset.row)].physical[Number(e.target.dataset.axis)] = e.target.value;
    dirty();
  };
  rows.onclick = e => {
    const button = e.target.closest('[data-delete]'); if (!button || controller) return;
    (mode === 'points' ? points : checks).splice(Number(button.dataset.delete), 1); dirty(); render();
  };
  for (const button of body.querySelectorAll('[data-ce-mode]')) button.onclick = () => { if (!controller) { mode = button.dataset.ceMode; render(); } };
  clear.onclick = () => { if (!controller) { points = []; checks = []; dirty(); render(); } };
  canvas.onclick = e => {
    if (!image || !canWrite || controller) return;
    const rect = canvas.getBoundingClientRect(), style = getComputedStyle(canvas);
    const left = parseFloat(style.borderLeftWidth) || 0, top = parseFloat(style.borderTopWidth) || 0;
    const width = rect.width - left - (parseFloat(style.borderRightWidth) || 0), height = rect.height - top - (parseFloat(style.borderBottomWidth) || 0);
    const pixel = [Math.round(Math.max(0, Math.min(canvas.width - 1, (e.clientX - rect.left - left) * canvas.width / width))), Math.round(Math.max(0, Math.min(canvas.height - 1, (e.clientY - rect.top - top) * canvas.height / height)))];
    if ([...points, ...checks].some(p => p.pixel[0] === pixel[0] && p.pixel[1] === pixel[1])) { error.textContent = '点位重复，请选择不同位置；验证点必须独立。'; return; }
    const list = mode === 'points' ? points : checks;
    if (list.length >= 100) { error.textContent = '每组最多100点。'; return; }
    list.push({ pixel, physical: ['', ''] }); dirty(); render();
  };
  unit.onchange = () => { tolerance.value = unit.value === 'mm' ? '1' : '0.01'; $('[data-ce-unit-label]', body).textContent = unit.value; dirty(); };
  for (const input of [scope, ref, tolerance]) input.oninput = dirty;
  fileInput.onchange = async () => {
    const token = ++generation; image = null; points = []; checks = []; canvas.hidden = true; dirty(); render();
    if (url) URL.revokeObjectURL(url); url = null;
    const file = fileInput.files?.[0]; if (!file) return;
    if (file.size > 10 * 1024 * 1024 || !/^image\/(png|jpeg|webp|bmp)$/.test(file.type)) { error.textContent = '请选择10MB以内的PNG/JPEG/WebP/BMP。'; return; }
    try {
      let hash = null;
      if (globalThis.crypto?.subtle) {
        const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
        hash = [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, '0')).join('');
      }
      if (closed || token !== generation) return;
      const candidate = new Image(); url = URL.createObjectURL(file); candidate.src = url; await candidate.decode();
      if (closed || token !== generation) return;
      if (candidate.naturalWidth * candidate.naturalHeight > 20000000 || candidate.naturalWidth < 2 || candidate.naturalHeight < 2) throw new Error('图片尺寸超出2–2000万像素范围。');
      image = candidate; canvas.width = image.naturalWidth; canvas.height = image.naturalHeight; canvas.hidden = false;
      if (!reference) ref.value = hash ? 'image-sha256:' + hash : '';
      mode = 'points'; render();
      if (!hash && !reference) { $('details', body).open = true; error.textContent = '当前HTTP页面无法生成安全指纹，请在图像参考中填写画面版本或固定参考编号。'; }
    } catch (e) { if (!closed && token === generation) error.textContent = e.message || '图片无法读取'; }
  };
  save.onclick = async () => {
    if (!canWrite || !image || controller) return;
    dirty();
    try {
      const convert = list => list.map(p => ({ pixel: p.pixel, physical: p.physical.map(v => {
        if (String(v).trim() === '' || !Number.isFinite(Number(v))) throw new Error('请填写每个点的实际X、Y坐标。');
        return Number(v);
      }) }));
      if (!scope.value.trim() || !ref.value.trim()) throw new Error('请填写来源范围与图像参考。');
      if (!(Number(tolerance.value) > 0)) throw new Error('允许误差必须大于0。');
      const payload = { scope_id: scope.value.trim(), reference: ref.value.trim(), image_size: [canvas.width, canvas.height], plane: unit.value === 'mm' ? 'workpiece' : 'ground', unit: unit.value, tolerance: Number(tolerance.value), points: convert(points), validation_points: convert(checks) };
      controller = new AbortController(); eligibility();
      for (const control of body.querySelectorAll('input,select,button')) control.disabled = true;
      const response = await fetch('/api/calibrations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: controller.signal });
      const result = await response.json(); if (closed) return;
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '点位或坐标不符合要求。');
      output.textContent = `${result.validated ? '验证通过' : '验证未通过 · 不能用于测量'} · 已保存版本 ${result.version}。最大验证误差 ${Number(result.validation_max).toFixed(4)} ${result.unit}，拟合误差 ${Number(result.fit_max).toFixed(4)} ${result.unit}。`;
      if (!result.validated) error.textContent = '误差超过允许值，请检查点位和实际坐标；该版本不可用于测量。';
      onSaved?.(result);
    } catch (e) { if (!closed && e.name !== 'AbortError') error.textContent = e.message; }
    finally { controller = null; if (!closed) { for (const control of body.querySelectorAll('input,select,button')) control.disabled = !canWrite; clear.disabled = !image; eligibility(); } }
  };
  return drawer;
}
