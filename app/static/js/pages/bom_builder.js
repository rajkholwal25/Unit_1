/**
 * BOM Builder — generate codes, pick RM from Item Master, push multi-level BOM to SAP.
 */
(function () {
  let state = null;
  let rmTimer;
  let createdLoaded = false;

  function toast(msg, type) {
    if (typeof showToast === 'function') showToast(msg, type || 'success');
    else alert(msg);
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  function debounce(fn, wait) {
    let t;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  function getYield() {
    return parseFloat(document.getElementById('yield_loss_pct').value, 10) || 2;
  }

  function updateButtons(ready) {
    document.getElementById('btn-save').disabled = !ready;
    document.getElementById('btn-push').disabled = !ready;
  }

  function setCreatedStatus(msg) {
    const el = document.getElementById('created-status');
    if (el) el.textContent = msg || '';
  }

  function renderCreatedBoms(items) {
    const tb = document.getElementById('created-boms-tbody');
    if (!tb) return;
    const rows = items || [];
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">No saved BOMs yet.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map((r) =>
      '<tr data-fg-id="' + esc(String(r.fg_id)) + '">' +
      '<td class="font-monospace small fw-medium">' + esc(r.fg_code) + '</td>' +
      '<td>' + esc(r.template || '—') + '</td>' +
      '<td class="font-monospace small">' + esc(r.raw_material_item_code || '—') + '</td>' +
      '<td>' + esc(String(r.yield_loss_pct ?? 2)) + '%</td>' +
      '<td class="small text-muted">' + esc((r.created_at || '').replace('T', ' ').slice(0, 19) || '—') + '</td>' +
      '<td class="text-end">' +
      '<button type="button" class="btn btn-sm btn-outline-secondary me-1 btn-view-bom">View</button>' +
      '<button type="button" class="btn btn-sm btn-outline-danger me-1 btn-del-local" title="Delete locally">Local</button>' +
      '<button type="button" class="btn btn-sm btn-outline-danger me-1 btn-del-sap" title="Delete BOM from SAP (ProductTrees)">SAP</button>' +
      '<button type="button" class="btn btn-sm btn-danger btn-del-both" title="Delete local + SAP BOM">Both</button>' +
      '</td></tr>'
    ).join('');
  }

  function renderCreatedDetail(detail) {
    const panel = document.getElementById('created-detail-panel');
    const fg = document.getElementById('detail-fg-code');
    const tb = document.getElementById('detail-lines-tbody');
    if (!panel || !fg || !tb) return;
    panel.style.display = 'block';
    fg.textContent = 'FG: ' + (detail.fg_code || '');
    const lines = detail.lines || [];
    if (!lines.length) {
      tb.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No lines saved.</td></tr>';
      return;
    }
    tb.innerHTML = lines.map((ln) =>
      '<tr>' +
      '<td class="font-monospace small">' + esc(ln.parent) + '</td>' +
      '<td class="font-monospace small">' + esc(ln.child) +
      (ln.line_type === 'raw_material' ? ' <span class="badge text-bg-warning text-dark">RM</span>' : '') +
      '</td>' +
      '<td>' + esc(String(ln.quantity ?? '—')) + '</td>' +
      '<td class="small">' + esc(ln.parent_warehouse || '—') + '</td>' +
      '<td class="small">' + esc(ln.child_warehouse || '—') + '</td>' +
      '</tr>'
    ).join('');
  }

  async function loadCreatedBoms() {
    setCreatedStatus('Loading…');
    try {
      const res = await fetch('/bom-builder/created');
      const body = await res.json();
      if (!res.ok) {
        setCreatedStatus('');
        toast(body.error || 'Load failed', 'danger');
        return;
      }
      renderCreatedBoms(body.items || []);
      setCreatedStatus('Loaded ' + (body.items?.length || 0));
      createdLoaded = true;
    } catch {
      setCreatedStatus('');
      toast('Load failed', 'danger');
    }
  }

  async function viewCreatedBom(fgId) {
    try {
      const res = await fetch('/bom-builder/created/' + encodeURIComponent(String(fgId)));
      const body = await res.json();
      if (!res.ok) {
        toast(body.error || 'Load failed', 'danger');
        return;
      }
      renderCreatedDetail(body);
    } catch {
      toast('Load failed', 'danger');
    }
  }

  function renderBomLines(lines) {
    const tbody = document.getElementById('bom-lines-tbody');
    if (!lines || !lines.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted text-center">Select raw material</td></tr>';
      updateButtons(false);
      return;
    }
    tbody.innerHTML = lines.map((ln) =>
      '<tr>' +
      '<td class="font-monospace small">' + esc(ln.parent) + '</td>' +
      '<td class="font-monospace small">' + esc(ln.child) +
      (ln.line_type === 'raw_material' ? ' <span class="badge text-bg-warning text-dark">RM</span>' : '') +
      '</td>' +
      '<td>' + esc(String(ln.quantity)) + '</td>' +
      '<td class="small">' + esc(ln.parent_warehouse) + '</td>' +
      '<td class="small">' + esc(ln.child_warehouse) + '</td>' +
      '</tr>'
    ).join('');
    updateButtons(state && state.bom && state.bom.ready);
  }

  async function fetchPatternSuggest(q) {
    try {
      const res = await fetch('/patterns/search?q=' + encodeURIComponent(q || ''));
      const data = await res.json();
      const ul = document.getElementById('pattern_suggestions');
      ul.innerHTML = '';
      (data.results || []).forEach((it) => {
        const li = document.createElement('li');
        li.className = 'list-group-item list-group-item-action';
        li.textContent = it.pattern_name + ' (' + it.pattern_code + ')';
        li.onclick = () => {
          document.getElementById('pattern_input').value = it.pattern_code;
          document.getElementById('pattern_id').value = it.id;
          ul.style.display = 'none';
        };
        ul.appendChild(li);
      });
      ul.style.display = (data.results || []).length ? 'block' : 'none';
    } catch {
      document.getElementById('pattern_suggestions').style.display = 'none';
    }
  }

  async function searchRm(term) {
    if (!state || !state.fg_code) return;
    const url = '/bom-builder/raw-materials?fg_code=' + encodeURIComponent(state.fg_code) +
      '&process_items=' + encodeURIComponent((state.process_items || []).join(',')) +
      '&q=' + encodeURIComponent(term || '');
    try {
      const res = await fetch(url);
      const body = await res.json();
      const ul = document.getElementById('rm_suggestions');
      const items = body.results || [];
      if (!items.length) {
        ul.style.display = 'none';
        return;
      }
      ul.innerHTML = items.map((it) =>
        '<li class="list-group-item list-group-item-action font-monospace small" data-code="' +
        esc(it.item_code) + '">' + esc(it.item_code) +
        ' <span class="text-muted">' + esc(it.item_name) + '</span></li>'
      ).join('');
      ul.style.display = 'block';
    } catch {
      document.getElementById('rm_suggestions').style.display = 'none';
    }
  }

  function selectRm(code) {
    document.getElementById('rm_code').value = code;
    document.getElementById('rm_search').value = code;
    document.getElementById('rm_selected').textContent = 'Selected: ' + code + ' (warehouse FBD-RM)';
    document.getElementById('rm_suggestions').style.display = 'none';
    if (state) {
      state.raw_material_item_code = code;
      refreshBomPreview();
    }
  }

  async function refreshBomPreview() {
    if (!state || !state.raw_material_item_code) return;
    try {
      const res = await fetch('/bom-builder/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fg_code: state.fg_code,
          processes: state.processes,
          raw_material_item_code: state.raw_material_item_code,
          yield_loss_pct: getYield(),
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        toast(body.error || 'BOM preview failed', 'danger');
        return;
      }
      state.bom = body;
      state.bom_chain = body.lines;
      renderBomLines(body.lines);
    } catch {
      toast('BOM preview failed', 'danger');
    }
  }

  async function generate() {
    const coating = document.getElementById('coating').value;
    const thicknessVal = parseFloat(document.getElementById('thickness').value, 10);
    if (!document.getElementById('material_type').value || !document.getElementById('pattern_id').value ||
        !document.getElementById('template_id').value || !coating) {
      toast('Select material, pattern, coating, and template', 'warning');
      return;
    }
    if (Number.isNaN(thicknessVal) || thicknessVal < 0) {
      toast('Thickness must be a valid number', 'warning');
      return;
    }
    const fd = new URLSearchParams();
    fd.append('material_type', document.getElementById('material_type').value);
    fd.append('thickness', document.getElementById('thickness').value);
    fd.append('pattern_id', document.getElementById('pattern_id').value);
    fd.append('coating', coating);
    fd.append('template_id', document.getElementById('template_id').value);
    const rm = document.getElementById('rm_code').value;
    if (rm) fd.append('raw_material_item_code', rm);

    try {
      const res = await fetch('/bom-builder/generate', { method: 'POST', body: fd });
      const d = await res.json();
      if (!res.ok) {
        toast(d.error || 'Generate failed', 'danger');
        return;
      }
      document.getElementById('preview').style.display = 'block';
      document.getElementById('fgcode').textContent = d.fg_code;
      const proc = document.getElementById('processes');
      proc.innerHTML = '';
      (d.process_items || []).forEach((p) => {
        const li = document.createElement('li');
        li.className = 'list-group-item font-monospace small';
        li.textContent = p;
        proc.appendChild(li);
      });
      state = {
        fg_code: d.fg_code,
        process_items: d.process_items,
        processes: d.processes,
        material_type: document.getElementById('material_type').value,
        thickness: thicknessVal,
        coating: coating,
        pattern_id: document.getElementById('pattern_id').value,
        template_id: d.template_id,
        raw_material_item_code: rm || '',
        bom: d.bom,
        bom_chain: d.bom ? d.bom.lines : [],
      };
      renderBomLines(state.bom_chain);
      if (rm) refreshBomPreview();
      document.getElementById('preview').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch {
      toast('Generate failed', 'danger');
    }
  }

  function payload() {
    return {
      fg_code: state.fg_code,
      process_items: state.process_items,
      material_type: state.material_type,
      thickness: state.thickness,
      coating: state.coating,
      pattern_id: state.pattern_id,
      template_id: state.template_id,
      raw_material_item_code: state.raw_material_item_code,
      yield_loss_pct: getYield(),
      bom_chain: state.bom_chain,
    };
  }

  async function saveLocal() {
    if (!state || !state.raw_material_item_code) {
      toast('Select raw material first', 'warning');
      return;
    }
    try {
      const res = await fetch('/bom-builder/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
      });
      const d = await res.json();
      if (res.ok) toast(d.message || 'Saved', 'success');
      else toast(d.error || 'Save failed', 'danger');
    } catch {
      toast('Save failed', 'danger');
    }
  }

  async function pushSap() {
    if (!state || !state.raw_material_item_code) {
      toast('Select raw material and save first', 'warning');
      return;
    }
    if (!confirm('Push to SAP?\n\nNew items will be created; existing items → BOM only.')) return;
    try {
      const res = await fetch('/bom-builder/push', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
      });
      const d = await res.json();
      if (res.ok) {
        const is = d.log?.items?.summary;
        const bs = d.log?.bom?.summary;
        let msg = 'SAP push OK';
        if (is) msg += ' — items: ' + is.created + ' created, ' + is.skipped + ' skipped';
        if (bs) msg += '; BOM: ' + bs.created + ' created, ' + bs.updated + ' updated';
        toast(msg, 'success');
      } else toast(d.error || 'Push failed', 'danger');
    } catch {
      toast('Push failed', 'danger');
    }
  }

  document.getElementById('btn-generate').addEventListener('click', generate);
  document.getElementById('btn-save').addEventListener('click', saveLocal);
  document.getElementById('btn-push').addEventListener('click', pushSap);

  document.getElementById('btn-sync-sap')?.addEventListener('click', async () => {
    if (!confirm('Sync complete Item Master from SAP into local DB?')) return;
    const btn = document.getElementById('btn-sync-sap');
    btn.disabled = true;
    const old = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Syncing…';
    try {
      const res = await fetch('/bom-builder/sync-item-master', { method: 'POST' });
      const body = await res.json();
      if (res.ok) {
        toast(body.message || 'Sync complete');
      } else {
        toast(body.error || 'Sync failed', 'danger');
      }
    } catch {
      toast('Sync failed', 'danger');
    } finally {
      btn.disabled = false;
      btn.innerHTML = old;
    }
  });

  document.getElementById('pattern_input').addEventListener('input', debounce(function () {
    document.getElementById('pattern_id').value = '';
    fetchPatternSuggest(this.value);
  }, 250));

  document.getElementById('rm_search').addEventListener('input', function () {
    clearTimeout(rmTimer);
    rmTimer = setTimeout(() => searchRm(this.value), 250);
  });

  document.getElementById('rm_suggestions').addEventListener('click', (e) => {
    const li = e.target.closest('li[data-code]');
    if (li) selectRm(li.getAttribute('data-code'));
  });

  document.getElementById('yield_loss_pct').addEventListener('change', () => {
    if (state && state.raw_material_item_code) refreshBomPreview();
  });

  document.getElementById('tab-created-btn')?.addEventListener('shown.bs.tab', () => {
    if (!createdLoaded) loadCreatedBoms();
  });

  document.getElementById('created-boms-tbody')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-view-bom');
    const tr = e.target.closest('tr[data-fg-id]');
    if (!tr) return;
    const fgId = tr.getAttribute('data-fg-id');
    if (btn) {
      viewCreatedBom(fgId);
      return;
    }
    const delLocal = e.target.closest('.btn-del-local');
    const delSap = e.target.closest('.btn-del-sap');
    const delBoth = e.target.closest('.btn-del-both');
    if (!delLocal && !delSap && !delBoth) return;
    const mode = delBoth ? 'both' : delSap ? 'sap' : 'local';
    const msg =
      mode === 'local' ? 'Delete this BOM locally only?' :
      mode === 'sap' ? 'Delete this BOM from SAP only? (Items are NOT deleted)' :
      'Delete locally AND delete BOM from SAP? (Items are NOT deleted)';
    if (!confirm(msg)) return;
    fetch('/bom-builder/created/' + encodeURIComponent(fgId) + '/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }).then(async (r) => {
      const ct = (r.headers.get('content-type') || '').toLowerCase();
      let payload;
      if (ct.includes('application/json')) {
        payload = await r.json();
      } else {
        const text = await r.text();
        payload = {
          error: text
            ? ('Request failed (HTTP ' + r.status + ', non-JSON response)')
            : ('Request failed (HTTP ' + r.status + ')'),
        };
        // If we got redirected to login/session page, prompt refresh.
        if (r.redirected) payload.error = 'Session expired — refresh the page and try again.';
      }
      return { ok: r.ok, status: r.status, payload };
    }).then(({ ok, status, payload }) => {
      if (!ok) {
        toast(payload.error || ('Delete failed (HTTP ' + status + ')'), 'danger');
        return;
      }
      toast('Deleted: ' + mode);
      // Refresh list + hide detail panel
      const dp = document.getElementById('created-detail-panel');
      if (dp) dp.style.display = 'none';
      loadCreatedBoms();
    }).catch(() => toast('Delete failed — refresh page once', 'danger'));
  });
})();
