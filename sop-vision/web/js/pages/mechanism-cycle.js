import { openDrawer } from '../ui.js';

/** Import is transient; closing cancels the request and ignores late results. */
export function openCycleAnalysis({onSaved}) {
  let closed = false;
  let busy = false;
  const controller = new AbortController();
  const drawer = openDrawer({title:'周期趋势',subtitle:'导入周期 CSV，观察相对参考期的变化',
    onClose:()=>{closed=true;controller.abort();},html:`<form class="ml-import ml-cycle-form" data-ml-form>
      <label>CSV 文件<input name="file" type="file" accept=".csv,text/csv" required></label>
      <p class="ml-help">表头 cycle,value · 至少 24 周期 · 每周期至少 4 点 · 最大 2 MB</p>
      <label>报告名称<input name="title" maxlength="200" placeholder="默认使用文件名"></label>
      <label>采样值单位<input name="unit" maxlength="40" placeholder="例如 A、°C、mm/s"></label>
      <details><summary>参考与分析参数</summary><p class="ml-help">起始参考期须为同工况正常数据，至少前 20 周期。</p>
        <label>参考期比例<input name="reference_fraction" type="number" min="0.01" max="0.5" step="0.01" value="0.2" required></label>
        <label>偏离阈值<input name="threshold" type="number" min="0.01" max="100" step="any" value="6" required></label>
        <label>连续超限周期<input name="hold" type="number" min="1" max="100" step="1" value="3" required></label>
        <label>外推周期<input name="horizon" type="number" min="1" max="20" step="1" value="5" required></label>
      </details><p class="ml-help">观察偏离与短期趋势，不估算剩余寿命。</p>
      <button class="btn btn-primary" type="submit">生成分析报告</button><p role="alert" data-cycle-error></p><p role="status" data-cycle-status></p></form>`});
  const form = drawer.body.querySelector('form');
  form.onsubmit = async event=>{
    event.preventDefault();
    if (busy) return;
    const error = form.querySelector('[data-cycle-error]');
    const status = form.querySelector('[data-cycle-status]');
    const file = form.elements.file.files[0];
    if (!file || file.size>2*1024*1024) {error.textContent='请选择不超过 2 MB 的 CSV 文件';return;}
    busy=true;
    error.textContent='';
    status.textContent='正在分析…';
    form.querySelectorAll('input,button').forEach(control=>control.disabled=true);
    try {
      const csv = await file.text();
      if (closed) return;
      const body={csv_text:csv,title:form.elements.title.value.trim()||file.name,unit:form.elements.unit.value.trim()};
      for (const key of ['reference_fraction','threshold','hold','horizon']) body[key]=Number(form.elements[key].value);
      const response=await fetch('/api/mechanism/analyses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
      const report=await response.json();
      if (!response.ok) throw new Error(typeof report.detail==='string'?report.detail:'数据或参数不符合要求');
      if (!closed) {drawer.close();onSaved(report);}
    } catch(exception) {
      if (!closed && exception.name!=='AbortError') {error.textContent=exception.message;status.textContent='';}
    } finally {
      busy=false;
      if (!closed) form.querySelectorAll('input,button').forEach(control=>control.disabled=false);
    }
  };
  return drawer;
}
