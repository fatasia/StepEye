import { $, esc } from "../util.js";
import { openDrawer, toast } from "../ui.js";

// 范围单独保存，日常分配项目不必重设用户密码。
export function openUserScope(user, request, onSaved) {
  let closed = false;
  const drawer = openDrawer({
    title: `${user.username} · 数据范围`,
    html: '<p>正在读取项目…</p>',
    onClose: () => { closed = true; },
  });
  request('/api/label-projects').then((projects) => {
    if (closed) return;
    const selected = new Set(user.project_ids || []);
    const rows = [...projects];
    for (const id of selected) {
      if (!rows.some(p => p.id === id)) rows.push({id, name: `${id}（项目不可用）`});
    }
    drawer.body.innerHTML = `<form class="settings-user-form">
      <label>访问范围<select name="mode"><option value="all">全部数据</option><option value="projects">指定项目及关联工位</option></select></label>
      <div data-scope-projects>${rows.length ? rows.map(p => `<label style="display:flex;align-items:center;gap:10px;margin:12px 0"><input style="width:auto;margin:0" type="checkbox" name="project" value="${esc(p.id)}" ${selected.has(p.id) ? 'checked' : ''}><span>${esc(p.name)}${p.scene_id ? ` · ${esc(p.scene_id)}` : ''}</span></label>`).join('') : '<p>尚无样本项目</p>'}</div>
      <p>指定项目时，未勾选表示无数据访问权。保存会撤销该账号旧会话和 MCP 授权。</p>
      <p>受限账号仅开放已支持项目隔离的功能；其他全局数据入口不可用。</p>
      <div class="settings-error" role="alert" data-scope-error hidden></div>
      <button class="btn btn-primary" type="submit">保存范围</button></form>`;
    const form = $('form', drawer.body);
    form.elements.mode.value = user.project_ids == null ? 'all' : 'projects';
    const refresh = () => { $('[data-scope-projects]', form).hidden = form.elements.mode.value === 'all'; };
    form.elements.mode.onchange = refresh;
    refresh();
    form.onsubmit = async (event) => {
      event.preventDefault();
      const button = $('button[type=submit]', form);
      button.disabled = true;
      const error = $('[data-scope-error]', form);
      error.hidden = true;
      try {
        const project_ids = form.elements.mode.value === 'all' ? null :
          [...form.querySelectorAll('[name=project]:checked')].map(el => el.value);
        await request(`/api/users/${encodeURIComponent(user.username)}/scope`, {
          method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project_ids}),
        });
        if (closed) return;
        drawer.close();
        await onSaved();
        toast('数据范围已更新');
      } catch (e) {
        if (!closed) { error.textContent = e.message; error.hidden = false; }
      } finally { button.disabled = false; }
    };
  }).catch((e) => { if (!closed) drawer.body.textContent = e.message; });
  return drawer;
}
