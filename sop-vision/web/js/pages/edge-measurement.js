import { $, esc } from '../util.js';
import { openDrawer } from '../ui.js';

const reasons = {
  roi_too_small: '选区太小，请扩大到至少 12 × 12 像素',
  multiple_edges: '选区存在多条强边缘，请缩小到单条边缘',
  weak_or_incomplete_edge: '边缘弱或不完整，请调整照明或选区',
  edge_direction_mismatch: '边缘方向与选择不符',
  edge_not_straight: '边缘不够直', non_parallel_edges: '两条边缘不平行',
  no_common_edge_span: '两条边缘没有共同覆盖区段', coincident_edges: '两条边缘重合',
  degenerate_physical_line: '物理线段退化，请检查标定'
};

/** A one-shot calibrated measurement, not a live inspection or production decision. */
export function openEdgeMeasurement(calibration) {
  let closed = false;
  let image = null;
  let file = null;
  let imageUrl = null;
  let rois = [];
  let start = null;
  let preview = null;
  let result = null;
  let controller = null;
  let generation = 0;
  const drawer = openDrawer({ title: '框选边缘测量',
    subtitle: `${esc(calibration.scope_id)} · 标定版本 ${calibration.version} · ${esc(calibration.unit)}`,
    html: `<style>.edge-measure [hidden]{display:none!important}.edge-measure label{display:block;margin:16px 0}.edge-measure canvas{width:100%;height:auto;display:block;touch-action:none;border:1px solid var(--border,#445);cursor:crosshair}.edge-measure select{padding:8px;background:var(--surface,#172027);color:inherit}.edge-measure .em-actions{display:flex;gap:8px;margin:16px 0;flex-wrap:wrap}.edge-measure output{display:block;font-size:22px;margin:18px 0}.edge-measure p{line-height:1.6}.edge-measure [role=alert]{color:#ff9890}</style>
      <section class="edge-measure"><p>上传与标定相同画面的图片，依次拖出两个选区，每个选区只包含一条直边。仅测量标定平面内的垂直间距。</p>
      <label>图片<input data-em-file type="file" accept="image/png,image/jpeg,image/webp,image/bmp"></label>
      <label>边缘方向 <select data-em-direction><option value="vertical">竖直边缘 · 测宽度</option><option value="horizontal">水平边缘 · 测高度</option></select></label>
      <p data-em-hint>请先选择图片（最多 10 MB）。</p><canvas data-em-canvas hidden aria-label="拖动绘制两个边缘选区"></canvas>
      <div class="em-actions"><button class="btn" data-em-reset disabled>重画选区</button><button class="btn btn-primary" data-em-run disabled>测量</button><button class="btn" data-em-cancel hidden>取消请求</button></div>
      <p role="alert" data-em-error></p><output data-em-result aria-live="polite"></output></section>`,
    onClose: () => {
      closed = true;
      generation++;
      controller?.abort();
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    }
  });
  const body = drawer.body;
  const canvas = $('[data-em-canvas]', body);
  const ctx = canvas.getContext('2d');
  const run = $('[data-em-run]', body);
  const reset = $('[data-em-reset]', body);
  const cancel = $('[data-em-cancel]', body);
  const input = $('[data-em-file]', body);
  const direction = $('[data-em-direction]', body);
  const error = $('[data-em-error]', body);
  const output = $('[data-em-result]', body);
  const hint = $('[data-em-hint]', body);
  function draw() {
    if (!image) return;
    ctx.drawImage(image, 0, 0);
    ctx.lineWidth = Math.max(1.5, canvas.width / 350);
    ctx.font = `${Math.max(14, canvas.width / 32)}px sans-serif`;
    [...rois, ...(preview ? [preview] : [])].forEach((r, i) => {
      ctx.strokeStyle = i === 0 ? '#38d5d0' : '#e6b34b';
      ctx.fillStyle = ctx.strokeStyle;
      ctx.strokeRect(r[0], r[1], r[2] - r[0], r[3] - r[1]);
      ctx.fillText(String(i + 1), r[0] + 4, r[1] + 20);
    });
    function line(points, color) {
      if (!points) return;
      ctx.strokeStyle = color;
      ctx.beginPath();
      ctx.moveTo(...points[0]);
      ctx.lineTo(...points[1]);
      ctx.stroke();
    }
    result?.edges?.forEach(edge => line(edge.line, '#78ff80'));
    line(result?.measurement_line, '#ff5e94');
  }
  function clear() {
    rois = [];
    result = null;
    start = null;
    preview = null;
    output.textContent = '';
    error.textContent = '';
    run.disabled = true;
    hint.textContent = '拖出第 1 个边缘选区。';
    draw();
  }
  function position(e) {
    const rect = canvas.getBoundingClientRect();
    return [Math.round(Math.max(0, Math.min(canvas.width, (e.clientX - rect.left) * canvas.width / rect.width))),
      Math.round(Math.max(0, Math.min(canvas.height, (e.clientY - rect.top) * canvas.height / rect.height)))];
  }
  function rectangle(a, b) {
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  }
  canvas.onpointerdown = e => {
    if (!image || controller || rois.length >= 2) return;
    start = position(e);
    canvas.setPointerCapture(e.pointerId);
  };
  canvas.onpointermove = e => {
    if (!start) return;
    preview = rectangle(start, position(e));
    draw();
  };
  canvas.onpointerup = e => {
    if (!start) return;
    const r = rectangle(start, position(e));
    start = null;
    preview = null;
    if (r[2] - r[0] < 12 || r[3] - r[1] < 12) error.textContent = reasons.roi_too_small;
    else {
      rois.push(r);
      error.textContent = '';
      hint.textContent = rois.length === 2 ? '两个选区已就绪；绿色为提取边缘，粉色为测量线。' : '拖出第 2 个边缘选区。';
    }
    run.disabled = rois.length !== 2;
    draw();
  };
  canvas.onpointercancel = () => { start = null; preview = null; draw(); };
  reset.onclick = clear;
  direction.onchange = () => { result = null; output.textContent = ''; draw(); };
  input.onchange = async () => {
    const token = ++generation;
    image = null;
    file = null;
    canvas.hidden = true;
    reset.disabled = true;
    clear();
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = null;
    try {
      const selected = input.files[0];
      if (!selected || selected.size > 10 * 1024 * 1024) throw new Error('请选择不超过 10 MB 的图片');
      imageUrl = URL.createObjectURL(selected);
      const loaded = new Image();
      loaded.src = imageUrl;
      await loaded.decode();
      // File selection/close can race the decoder; never resurrect stale images.
      if (closed || token !== generation) return;
      const [width, height] = calibration.image_size;
      if (loaded.naturalWidth !== width || loaded.naturalHeight !== height) throw new Error(`图片尺寸须与标定一致：${width} × ${height}`);
      image = loaded;
      file = selected;
      canvas.width = width;
      canvas.height = height;
      canvas.hidden = false;
      reset.disabled = false;
      draw();
    } catch (e) { if (!closed && token === generation) error.textContent = e.message; }
  };
  cancel.onclick = () => controller?.abort();
  run.onclick = async () => {
    if (!file || rois.length !== 2 || controller) return;
    controller = new AbortController();
    run.disabled = reset.disabled = input.disabled = direction.disabled = true;
    cancel.hidden = false;
    error.textContent = '';
    output.textContent = '正在测量…';
    const timeout = setTimeout(() => controller?.abort(), 30000);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('spec', JSON.stringify({ scope_id: calibration.scope_id, reference: calibration.reference,
        rois, edge_direction: direction.value }));
      const response = await fetch(`/api/calibrations/${encodeURIComponent(calibration.id)}/measure-edges`,
        { method: 'POST', body: form, signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '测量请求失败');
      if (closed) return;
      result = data;
      output.textContent = data.status === 'measured'
        ? `${Number(data.distance).toFixed(3)} ${data.unit} · 请复核边缘位置`
        : `待复核：${reasons[data.reason] || data.reason || '未获得有效测量'}`;
      draw();
    } catch (e) {
      if (!closed) { output.textContent = ''; error.textContent = e.name === 'AbortError' ? '测量已取消或超时，可重新发起。' : e.message; }
    } finally {
      clearTimeout(timeout);
      controller = null;
      if (!closed) { run.disabled = reset.disabled = input.disabled = direction.disabled = false; cancel.hidden = true; }
    }
  };
  return drawer;
}
