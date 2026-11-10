import { openDrawer } from '../ui.js';

const reasons = { low_contrast:'对比度不足', no_complete_pins:'没有完整可分辨的针脚',
  too_many_components:'分量过多', pin_touches_roi_border:'针脚接触选区边界，请扩大选区',
  pin_too_thin:'针脚像素宽度不足', incomplete_pin:'针脚不完整',
  partial_or_tilted_pins:'存在遮挡、倾斜或多排结构', reference_count_mismatch:'参考图提取数量与预期不同',
  tolerance_overlaps_pin_positions:'位置容差过大，无法区分相邻针脚' };

/** Fixed-fixture comparison; all distances are pixels, not physical metrology. */
export function openPinInspection() {
  let closed = false;
  let controller = null;
  let roi = null;
  let start = null;
  let preview = null;
  let result = null;
  const images = {};
  const urls = {};
  const generations = {};
  const drawer = openDrawer({ title:'连接器针脚检查', subtitle:'参考图 + 单排 ROI · 按需检查',
    html:`<style>.pin-check [hidden]{display:none!important}.pin-check label{display:block;margin:16px 0;font-size:13px}.pin-check input,.pin-check select{display:block;box-sizing:border-box;width:100%;min-height:38px;margin-top:8px;padding:9px 11px;border:1px solid var(--border,#34434b);border-radius:6px;background:var(--surface,#172027);color:inherit;font:inherit}.pin-check input:focus,.pin-check select:focus{outline:2px solid var(--accent,#3ec6c1);outline-offset:2px}.pin-check input::file-selector-button{padding:5px 10px;margin-right:12px;border:1px solid var(--border,#34434b);border-radius:4px;background:var(--surface,#172027);color:inherit;font:inherit;cursor:pointer}.pin-check input[type=file]{padding:6px;font-size:12px}.pin-check input:disabled,.pin-check select:disabled{opacity:.55}.pin-check details{margin:18px 0}.pin-check summary{cursor:pointer;color:var(--muted,#95a6ad)}.pin-check canvas{width:100%;height:auto;display:block;touch-action:none;border:1px solid #456;cursor:crosshair}.pin-check p{line-height:1.6}.pin-check .pin-actions{display:flex;gap:10px;margin:16px 0}.pin-check output{display:block;white-space:pre-wrap;line-height:1.7}.pin-check [role=alert]{color:#ff9890}</style>
    <div class="pin-check"><p>适用于固定夹具、相同尺寸视角的单排高对比针脚。选区覆盖针脚直身、避开端部与外壳；参考与待检图不做自动对齐。间距单位为像素，不测高度或内部压接。</p>
    <label>参考图片<input type="file" name="reference" accept="image/png,image/jpeg,image/webp,image/bmp"></label>
    <label>待检图片<input type="file" name="file" accept="image/png,image/jpeg,image/webp,image/bmp"></label>
    <label>排列方向<select name="axis"><option value="x">横向排列</option><option value="y">纵向排列</option></select></label>
    <label>针脚亮暗<select name="polarity"><option value="bright">亮针脚 / 暗背景</option><option value="dark">暗针脚 / 亮背景</option></select></label>
    <label>预期数量<input type="number" name="count" min="1" max="128" step="1" value="4"></label>
    <details><summary>像素容差</summary><label>位置容差<input type="number" name="position" min="0" max="100" step="0.1" value="3"> px</label><label>间距容差<input type="number" name="spacing" min="0" max="100" step="0.1" value="3"> px</label></details>
    <p data-pin-hint>选择参考图后，拖动框选一排针脚。</p><canvas data-pin-canvas hidden aria-label="在参考图拖动绘制针脚选区"></canvas>
    <div class="pin-actions"><button class="btn" data-pin-reset>重画</button><button class="btn btn-primary" data-pin-run disabled>检查针脚</button><button class="btn" data-pin-cancel hidden>取消</button></div>
    <p data-pin-error role="alert"></p><output data-pin-result aria-live="polite"></output></div>`,
    onClose:()=>{
      closed = true;
      controller?.abort();
      Object.values(urls).forEach(url=>URL.revokeObjectURL(url));
    }
  });
  const body = drawer.body;
  const find = s=>body.querySelector(s);
  const canvas = find('canvas');
  const context = canvas.getContext('2d');
  const run = find('[data-pin-run]');
  const error = find('[data-pin-error]');
  const output = find('output');
  function ready() { run.disabled = !roi || !images.reference || !images.file || !!controller; }
  function draw() {
    if (!images.reference) return;
    context.drawImage(result ? images.file : images.reference,0,0);
    context.lineWidth = Math.max(1.5,canvas.width/300);
    const box = preview || roi;
    if (!box) return;
    context.strokeStyle = '#39cece';
    context.strokeRect(box[0],box[1],box[2]-box[0],box[3]-box[1]);
    const horizontal = find('[name=axis]').value === 'x';
    function marker(center,color) {
      context.strokeStyle=color;
      context.beginPath();
      if(horizontal) { context.moveTo(center,box[1]);context.lineTo(center,box[3]); }
      else { context.moveTo(box[0],center);context.lineTo(box[2],center); }
      context.stroke();
    }
    result?.observed?.centers.forEach(c=>marker(c,'#88ed74'));
    result?.missing?.forEach(p=>marker(p.center,'#ff6677'));
  }
  function clear() {
    roi=null;preview=null;start=null;result=null;
    output.textContent='';error.textContent='';
    find('[data-pin-hint]').textContent='在参考图拖动框选一排针脚。';
    ready();draw();
  }
  for(const name of ['reference','file']) find(`[name=${name}]`).onchange=async()=>{
    const token=(generations[name]||0)+1;
    generations[name]=token;
    images[name]=null;
    if(urls[name]) URL.revokeObjectURL(urls[name]);
    clear();
    if(name==='reference') canvas.hidden=true;
    try {
      const file=find(`[name=${name}]`).files[0];
      if(!file || file.size>10*1024*1024) throw new Error('请选择不超过 10 MB 的单帧图片');
      urls[name]=URL.createObjectURL(file);
      const image=new Image();image.src=urls[name];await image.decode();
      if(closed || generations[name]!==token) return;
      if(image.naturalWidth*image.naturalHeight>4000000 || Math.max(image.naturalWidth,image.naturalHeight)>4096) throw new Error('图片最多 400 万像素、边长 4096');
      const other=images[name==='reference'?'file':'reference'];
      if(other && (other.naturalWidth!==image.naturalWidth || other.naturalHeight!==image.naturalHeight)) throw new Error('两张图片尺寸必须一致');
      images[name]=image;
      if(name==='reference') { canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.hidden=false; }
      ready();draw();
    }catch(e){if(!closed && generations[name]===token) error.textContent=e.message;}
  };
  function point(e) {
    const box=canvas.getBoundingClientRect();
    return [Math.round(Math.max(0,Math.min(canvas.width,(e.clientX-box.x)*canvas.width/box.width))),
      Math.round(Math.max(0,Math.min(canvas.height,(e.clientY-box.y)*canvas.height/box.height)))];
  }
  function rectangle(a,b) { return [Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.max(a[0],b[0]),Math.max(a[1],b[1])]; }
  canvas.onpointerdown=e=>{
    if(controller || !images.reference) return;
    result=null;output.textContent='';start=point(e);canvas.setPointerCapture(e.pointerId);
  };
  canvas.onpointermove=e=>{if(start){preview=rectangle(start,point(e));draw();}};
  canvas.onpointerup=e=>{
    if(!start) return;
    const box=rectangle(start,point(e));start=null;preview=null;
    if(Math.min(box[2]-box[0],box[3]-box[1])<12) error.textContent='选区至少 12 × 12 像素';
    else {roi=box;error.textContent='';find('[data-pin-hint]').textContent='选区已就绪。检查后显示待检图：绿线是实测中心，红线是缺失位置。';}
    ready();draw();
  };
  canvas.onpointercancel=()=>{start=null;preview=null;draw();};
  find('[data-pin-reset]').onclick=clear;
  for(const control of body.querySelectorAll('select,input[type=number]')) control.onchange=()=>{result=null;output.textContent='';draw();};
  find('[data-pin-cancel]').onclick=()=>controller?.abort();
  run.onclick=async()=>{
    if(run.disabled) return;
    const fields=['count','position','spacing'].map(name=>find(`[name=${name}]`));
    if(fields.some(field=>!field.reportValidity() || !field.value.trim())) return;
    controller=new AbortController();
    body.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
    const cancel=find('[data-pin-cancel]');cancel.hidden=false;cancel.disabled=false;
    error.textContent='';output.textContent='正在检查…';
    const timeout=setTimeout(()=>controller?.abort(),30000);
    try {
      const form=new FormData();
      for(const name of ['reference','file']) form.append(name,find(`[name=${name}]`).files[0]);
      form.append('spec',JSON.stringify({roi,axis:find('[name=axis]').value,polarity:find('[name=polarity]').value,
        expected_count:Number(fields[0].value),position_tolerance_px:Number(fields[1].value),spacing_tolerance_px:Number(fields[2].value)}));
      const response=await fetch('/api/quality/pins/inspect',{method:'POST',body:form,signal:controller.signal});
      const data=await response.json();
      if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'检查失败');
      if(closed) return;
      result=data;
      output.textContent=data.status==='review'?`待复核：${reasons[data.reason]||data.reason}`:
        `${data.status==='matched'?'与参考一致':'发现差异'} · 仍需复核\n数量：参考 ${data.reference.centers.length} / 实测 ${data.observed.centers.length}\n间距：${data.observed.spacing_px.map(n=>n.toFixed(2)).join(' / ')||'—'} px\n缺失位置：${data.missing.map(p=>`第 ${p.index} 针 (${p.center.toFixed(1)} px)`).join('、')||'无'}\n多余位置：${data.extra.join('、')||'无'}\n位置超差：${data.offsets.filter(p=>!p.within_tolerance).map(p=>`第 ${p.index} 针 ${p.delta_px.toFixed(2)} px`).join('、')||'无'}\n间距超差：${data.spacing_checks.filter(p=>!p.within_tolerance).map(p=>`${p.from_index}–${p.to_index} 针 ${p.delta_px.toFixed(2)} px`).join('、')||'无'}`;
      draw();
    } catch(e){if(!closed){output.textContent='';error.textContent=e.name==='AbortError'?'检查已取消或超时。':e.message;}}
    finally {
      clearTimeout(timeout);controller=null;
      if(!closed){body.querySelectorAll('input,select,button').forEach(el=>el.disabled=false);cancel.hidden=true;ready();}
    }
  };
  return drawer;
}
