import { openDrawer } from '../ui.js';
import { $, esc } from '../util.js';

async function request(path, options) {
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '配置无效，请检查成员与标定');
  return value;
}

/** Manual refresh only: no camera subscriptions or background polling. */
export function openCrossCamera() {
  let closed = false, sequence = 0, calibrations = [], stations = [];
  const drawer = openDrawer({title: '多机位观测', subtitle: '地面几何关联 · 与库存对账分开',
    onClose: () => { closed = true; sequence++; },
    html: `<style>.cc-panel{display:grid;gap:18px}.cc-panel label{display:grid;gap:7px}.cc-panel input,.cc-panel select{width:100%;min-width:0;box-sizing:border-box;padding:9px;background:var(--bg,#172126);color:inherit;border:1px solid var(--line,#364348);border-radius:6px}.cc-panel .cc-row{display:flex;gap:10px;align-items:center}.cc-panel .cc-row>select{flex:1}.cc-panel .cc-metrics{display:flex;gap:30px;padding:18px 0;border-block:1px solid var(--line,#364348)}.cc-panel .cc-metrics strong{display:block;font-size:28px}.cc-panel .cc-member{display:grid;grid-template-columns:20px 1fr 1fr;align-items:center;gap:9px;padding:8px 0}.cc-panel ul{padding-left:20px;line-height:1.8}.cc-panel details>div{display:grid;gap:14px;padding-top:16px}</style>
    <section class="cc-panel"><div class="cc-row"><select data-cc-group aria-label="多机位组"><option value="">正在读取…</option></select><button class="btn" data-cc-refresh>刷新</button><button class="btn" data-cc-delete hidden>删除</button></div>
    <div data-cc-result aria-live="polite">选择已配置的地面观测组。</div>
    <details data-cc-config hidden><summary>配置观测组</summary><div>
    <label>组名称<input data-cc-name placeholder="如 一层通道"></label>
    <label>共同地面坐标系<input data-cc-frame placeholder="如 factory-floor-1"></label>
    <p class="muted">所选标定必须使用同一地面原点、方向和米制单位；坐标系名称不会自动转换坐标。</p>
    <label>识别类别<input data-cc-labels value="person" placeholder="person,forklift"></label>
    <div data-cc-members></div><button class="btn btn-primary" data-cc-save>保存为新组</button>
    </div></details><p data-cc-error role="alert" style="color:var(--danger,#ff9890)"></p></section>`});
  const body = drawer.body;
  const error = (e) => { if (!closed) $('[data-cc-error]', body).textContent = e?.message || ''; };
  const reason = {unavailable:'暂无有效画面', stale:'观测已过期', labels_unavailable:'缺少所需识别类别',
    independent_time_axis:'视频文件不参与实时关联', frame_mismatch:'画面尺寸不匹配'};
  async function refresh() {
    const id = $('[data-cc-group]', body).value, seq = ++sequence;
    if (!id) { $('[data-cc-result]', body).textContent = '还没有观测组。管理员可在下方配置。'; return; }
    error();
    try {
      const result = await request(`/api/cross-camera/groups/${encodeURIComponent(id)}/snapshot`);
      if (closed || seq !== sequence) return;
      const valid = result.status === 'candidates';
      $('[data-cc-result]', body).innerHTML = `<h3>${valid ? '几何关联候选' : '观测不完整'}</h3>
      <div class="cc-metrics"><div><strong>${esc(String(result.visible_count))}</strong><span class="muted">各机位观测合计</span></div><div><strong>${valid ? result.candidate_groups.length : '—'}</strong><span class="muted">候选关联组</span></div><div><strong>${valid ? result.ambiguous_observations.length : '—'}</strong><span class="muted">歧义观测</span></div></div>
      <ul>${result.members.map(m => `<li>${esc(m.station)} · ${m.status === 'observed' ? `${m.visible_count} 个目标` : esc(reason[m.reason] || '标定或观测不可用')}</li>`).join('')}</ul>
      ${valid && result.candidate_groups.length ? `<ul>${result.candidate_groups.map(group => `<li>${group.map(i => {const p=result.observations[i]; return `${esc(p.station)} #${esc(String(p.track_id))}`;}).join(' ↔ ')}</li>`).join('')}</ul>` : ''}
      <p class="muted">${result.reason === 'receipt_skew' ? '画面接收时间差过大。' : ''}按服务接收时间对齐，不代表同步曝光。候选不计入确认库存，不提供跨镜头身份识别。</p>`;
    } catch (e) { if (seq === sequence) error(e); }
  }
  async function loadGroups(selected = '') {
    const groups = await request('/api/cross-camera/groups');
    if (closed) return;
    $('[data-cc-group]', body).innerHTML = groups.length ? groups.map(g => `<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('') : '<option value="">暂无观测组</option>';
    if (selected) $('[data-cc-group]', body).value = selected;
    await refresh();
  }
  $('[data-cc-group]', body).onchange = refresh;
  $('[data-cc-refresh]', body).onclick = refresh;
  $('[data-cc-delete]', body).onclick = async () => {
    const id = $('[data-cc-group]', body).value;
    if (!id || !confirm('删除此观测组？不影响标定和库存记录。')) return;
    try { await request(`/api/cross-camera/groups/${encodeURIComponent(id)}`, {method:'DELETE'}); await loadGroups(); }
    catch(e) { error(e); }
  };
  $('[data-cc-save]', body).onclick = async () => {
    const button = $('[data-cc-save]', body); button.disabled = true; error();
    try {
      const members = [...body.querySelectorAll('[data-cc-member]:checked')].map(input => {
        const c = calibrations[Number(input.value)];
        return {station: input.parentElement.querySelector('select').value, calibration_id:c.id,
          scope_id:c.scope_id, reference:c.reference, image_size:c.image_size};
      });
      const name = $('[data-cc-name]', body).value.trim(), frame = $('[data-cc-frame]', body).value.trim();
      if (!name || !frame || members.length < 2) throw new Error('填写组名称、共同坐标系，并选择至少两个成员');
      const labels = $('[data-cc-labels]', body).value.split(/[,，]/).map(v=>v.trim()).filter(Boolean);
      const id = `group-${crypto.randomUUID()}`;
      await request(`/api/cross-camera/groups/${id}`, {method:'PUT', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name, coordinate_frame_id:frame, members, labels})});
      if (!closed) { $('[data-cc-config]', body).open = false; await loadGroups(id); }
    } catch(e) { error(e); } finally { if (!closed) button.disabled = false; }
  };
  (async () => {
    try {
      await loadGroups();
      const user = await request('/api/auth/me');
      if (closed || user.role !== 'admin') return;
      $('[data-cc-delete]', body).hidden = false;
      [calibrations, stations] = await Promise.all([request('/api/calibrations'), request('/api/stations')]);
      if (closed) return;
      calibrations = calibrations.filter(c=>c.validated && c.plane === 'ground' && c.unit === 'm');
      $('[data-cc-members]', body).innerHTML = calibrations.length ? calibrations.map((c,i)=>`<label class="cc-member"><input type="checkbox" data-cc-member value="${i}"><span>${esc(c.scope_id)} · v${c.version}</span><select aria-label="标定对应工位">${stations.map(s=>`<option value="${esc(s.id)}" ${s.id===c.scope_id?'selected':''}>${esc(s.id)}</option>`).join('')}</select></label>`).join('') : '<p class="muted">先为至少两个工位完成米制地面标定。</p>';
      $('[data-cc-config]', body).hidden = false;
    } catch(e) { error(e); }
  })();
  return drawer;
}
