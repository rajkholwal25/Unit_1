/**
 * Item Master — local catalog + SAP sync, edit, delete (FG cascades to components).
 */
(function () {
  const table = document.getElementById('item-master-table');
  const variantsTable = document.getElementById('bom-variants-table');
  const searchInput = document.getElementById('search-q');
  const existsResult = document.getElementById('exists-result');
  const checkBtn = document.getElementById('btn-check-exists');

  const sapTbody = document.getElementById('sap-items-tbody');
  const sapStatus = document.getElementById('sap-list-status');
  const sapSearch = document.getElementById('sap-search-q');
  const btnSapSearch = document.getElementById('btn-sap-search');
  const btnSapSync = document.getElementById('btn-sap-sync-all');
  const btnSapPrev = document.getElementById('btn-sap-prev');
  const btnSapNext = document.getElementById('btn-sap-next');
  const editModalEl = document.getElementById('sap-edit-modal');
  const editModal = editModalEl && typeof bootstrap !== 'undefined'
    ? new bootstrap.Modal(editModalEl)
    : null;

  let sapSkip = 0;
  const sapTop = 50;
  let sapHasMore = false;

  function toast(msg, type) {
    if (typeof showToast === 'function') showToast(msg, type || 'success');
    else alert(msg);
  }

  function copyCode(code) {
    navigator.clipboard.writeText(code).then(() => toast('Copied: ' + code)).catch(() => alert('Copy failed'));
  }

  async function checkExists() {
    const code = (searchInput?.value || '').trim();
    if (!code) {
      existsResult.innerHTML = '<span class="text-warning">Enter an item code first.</span>';
      return;
    }
    existsResult.innerHTML = '<span class="text-muted">Checking…</span>';
    try {
      const res = await fetch('/item-codes/ajax_search?code=' + encodeURIComponent(code));
      const body = await res.json();
      existsResult.innerHTML = body.exists
        ? '<span class="text-success"><i class="bi bi-check-circle me-1"></i>' + body.message + '</span>'
        : '<span class="text-danger"><i class="bi bi-x-circle me-1"></i>' + body.message + '</span>';
    } catch {
      existsResult.innerHTML = '<span class="text-danger">Check failed.</span>';
    }
  }

  async function deleteBomVariant(fgId, code) {
    if (!confirm('Remove saved BOM setup for "' + code + '"?\n\nSAP items are not deleted.')) return;
    try {
      const res = await fetch('/item-codes/ajax_delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fg_id: Number(fgId) }),
      });
      const body = await res.json();
      if (res.ok) {
        document.querySelectorAll("tr[data-fg-id='" + fgId + "']").forEach((tr) => tr.remove());
        toast('BOM setup removed');
      } else alert(body.error || 'Delete failed');
    } catch {
      alert('Request failed');
    }
  }

  async function deleteSapFg(code) {
    let compList = '';
    try {
      const cr = await fetch('/item-codes/sap/components?fg_code=' + encodeURIComponent(code));
      const cd = await cr.json();
      if (cd.components && cd.components.length) {
        compList = '\n\nComponents:\n' + cd.components.join('\n');
      }
    } catch { /* ignore */ }

    if (!confirm(
      'Delete from SAP and local catalog?\n\nFG: ' + code + compList +
      '\n\nThis cannot be undone if SAP allows deletion.'
    )) {
      return;
    }

    try {
      const res = await fetch('/item-codes/sap/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fg_code: code, cascade: true }),
      });
      const body = await res.json();
      if (res.ok) {
        const n = (body.deleted || []).length;
        toast('Deleted ' + n + ' item(s) from SAP');
        document.querySelectorAll("tr[data-code='" + code + "']").forEach((tr) => tr.remove());
        if (sapTbody) loadSapItems(sapSkip);
      } else {
        alert(body.error || (body.errors && body.errors[0]?.error) || 'Delete failed');
      }
    } catch {
      alert('SAP delete failed');
    }
  }

  async function deleteSapItem(code) {
    if (!confirm('Delete only "' + code + '" from SAP and local catalog?')) return;
    try {
      const res = await fetch('/item-codes/sap/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_code: code, cascade: false }),
      });
      const body = await res.json();
      if (res.ok) {
        toast('Deleted ' + code);
        loadSapItems(sapSkip);
      } else alert(body.error || 'Delete failed');
    } catch {
      alert('Delete failed');
    }
  }

  function renderSapRows(items) {
    if (!sapTbody) return;
    if (!items.length) {
      sapTbody.innerHTML =
        '<tr><td colspan="8" class="text-center text-muted py-4">No items found.</td></tr>';
      return;
    }
    sapTbody.innerHTML = items.map((it) => {
      const isFg = it.role === 'fg';
      const groupLabel = it.items_group_code === 100 ? 'Finish Goods' :
        it.items_group_code === 107 ? 'Raw Material - Film' : it.items_group_code;
      return (
        '<tr data-code="' + escapeHtml(it.item_code) + '" data-name="' + escapeHtml(it.item_name) + '"' +
        ' data-group="' + it.items_group_code + '" data-role="' + it.role + '">' +
        '<td class="font-monospace small fw-medium">' + escapeHtml(it.item_code) + '</td>' +
        '<td class="small">' + escapeHtml(it.item_name) + '</td>' +
        '<td>' + escapeHtml(String(groupLabel)) + '</td>' +
        '<td>' + escapeHtml(it.material_type_label || '—') + '</td>' +
        '<td>' + escapeHtml(it.inventory_uom || '—') + '</td>' +
        '<td>' + (isFg ? '<span class="badge text-bg-primary">FG</span>' :
          '<span class="badge text-bg-secondary">Component</span>') + '</td>' +
        '<td class="text-center"><input type="checkbox" class="form-check-input sap-row-select" value="' +
        escapeHtml(it.item_code) + '"></td>' +
        '<td class="text-end text-nowrap">' +
        '<button type="button" class="btn btn-sm btn-outline-secondary me-1 btn-sap-edit" title="Edit">' +
        '<i class="bi bi-pencil"></i></button>' +
        '<button type="button" class="btn btn-sm btn-outline-danger btn-sap-delete" title="Delete this item only">' +
        '<i class="bi bi-trash"></i></button>' +
        (isFg ? '<button type="button" class="btn btn-sm btn-outline-danger ms-1 btn-sap-delete-fg" title="Delete FG and all components">' +
        '<i class="bi bi-trash3"></i></button>' : '') +
        '</td></tr>'
      );
    }).join('');
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  async function loadSapItems(skip) {
    if (!sapTbody) return;
    sapSkip = skip || 0;
    sapStatus.textContent = 'Loading…';
    const q = (sapSearch?.value || '').trim();
    const url = '/item-codes/sap/list?skip=' + sapSkip + '&top=' + sapTop + (q ? '&q=' + encodeURIComponent(q) : '');
    try {
      const res = await fetch(url);
      const body = await res.json();
      if (!res.ok) {
        sapStatus.textContent = '';
        sapTbody.innerHTML = '<tr><td colspan="8" class="text-danger py-3">' + escapeHtml(body.error || 'Load failed') + '</td></tr>';
        return;
      }
      sapHasMore = !!body.has_more;
      renderSapRows(body.items || []);
      sapStatus.textContent = 'Showing ' + (body.items?.length || 0) + ' items (from ' + sapSkip + ')';
      if (btnSapPrev) btnSapPrev.disabled = sapSkip <= 0;
      if (btnSapNext) btnSapNext.disabled = !sapHasMore;
      updateDeleteSelectedBtn();
    } catch {
      sapStatus.textContent = '';
      sapTbody.innerHTML = '<tr><td colspan="8" class="text-danger py-3">Could not reach SAP.</td></tr>';
    }
  }

  function openEditModal(row) {
    if (!editModal) return;
    document.getElementById('edit-item-code').value = row.getAttribute('data-code');
    document.getElementById('edit-item-code-display').value = row.getAttribute('data-code');
    document.getElementById('edit-item-name').value = row.getAttribute('data-name') || '';
    document.getElementById('edit-items-group').value = row.getAttribute('data-group') || '100';
    editModal.show();
  }

  async function saveSapEdit() {
    const code = document.getElementById('edit-item-code').value;
    const name = document.getElementById('edit-item-name').value;
    const group = parseInt(document.getElementById('edit-items-group').value, 10);
    try {
      const res = await fetch('/item-codes/sap/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_code: code, item_name: name, items_group_code: group }),
      });
      const body = await res.json();
      if (res.ok) {
        editModal.hide();
        toast('Updated ' + code + ' in SAP');
        loadSapItems(sapSkip);
      } else alert(body.error || 'Update failed');
    } catch {
      alert('Update failed');
    }
  }

  async function syncAllFromSap() {
    if (!confirm('Import/update all SAP items into local catalog? This may take a minute.')) return;
    btnSapSync.disabled = true;
    btnSapSync.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Syncing…';
    try {
      const res = await fetch('/item-codes/sap/sync', { method: 'POST' });
      const body = await res.json();
      if (res.ok) toast(body.message || 'Sync complete');
      else alert(body.error || 'Sync failed');
    } catch {
      alert('Sync failed');
    } finally {
      btnSapSync.disabled = false;
      btnSapSync.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i> Sync from SAP';
    }
  }

  checkBtn?.addEventListener('click', (e) => { e.preventDefault(); checkExists(); });

  variantsTable?.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-delete-variant');
    if (!btn) return;
    const row = btn.closest('tr');
    deleteBomVariant(row.getAttribute('data-fg-id'), row.getAttribute('data-code'));
  });

  table?.addEventListener('click', (e) => {
    const copyBtn = e.target.closest('.btn-copy-item');
    if (copyBtn) {
      e.preventDefault();
      copyCode(copyBtn.closest('tr').getAttribute('data-code'));
      return;
    }
    const delOne = e.target.closest('.btn-delete-sap-one');
    if (delOne) {
      e.preventDefault();
      deleteSapItem(delOne.closest('tr').getAttribute('data-code'));
      return;
    }
    const delFg = e.target.closest('.btn-delete-sap-fg-cascade');
    if (delFg) {
      e.preventDefault();
      deleteSapFg(delFg.closest('tr').getAttribute('data-code'));
    }
  });

  sapTbody?.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-code]');
    if (!row) return;
    const code = row.getAttribute('data-code');
    if (e.target.closest('.btn-sap-edit')) {
      e.preventDefault();
      openEditModal(row);
      return;
    }
    if (e.target.closest('.btn-sap-delete-fg')) {
      e.preventDefault();
      deleteSapFg(code);
      return;
    }
    if (e.target.closest('.btn-sap-delete')) {
      e.preventDefault();
      deleteSapItem(code);
    }
  });

  function getSelectedSapCodes() {
    if (!sapTbody) return [];
    return Array.from(sapTbody.querySelectorAll('.sap-row-select:checked'))
      .map((cb) => cb.value)
      .filter(Boolean);
  }

  async function deleteSelectedSapItems() {
    const codes = getSelectedSapCodes();
    if (!codes.length) {
      toast('Select at least one item (checkbox).', 'warning');
      return;
    }
    if (!confirm('Delete ' + codes.length + ' selected item(s) only?\n\n' + codes.join('\n'))) return;
    let ok = 0;
    let failed = null;
    for (const code of codes) {
      try {
        const res = await fetch('/item-codes/sap/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_code: code, cascade: false }),
        });
        const body = await res.json();
        if (res.ok) ok += 1;
        else failed = body.error || 'Delete failed';
      } catch {
        failed = 'Request failed';
        break;
      }
    }
    if (failed) alert(failed);
    else toast('Deleted ' + ok + ' item(s)');
    loadSapItems(sapSkip);
  }

  const btnDeleteSelected = document.getElementById('btn-sap-delete-selected');

  function updateDeleteSelectedBtn() {
    if (!btnDeleteSelected) return;
    btnDeleteSelected.disabled = getSelectedSapCodes().length === 0;
  }

  document.getElementById('btn-sap-delete-selected')?.addEventListener('click', deleteSelectedSapItems);
  document.getElementById('sap-select-all')?.addEventListener('change', (e) => {
    const on = e.target.checked;
    sapTbody?.querySelectorAll('.sap-row-select').forEach((cb) => { cb.checked = on; });
    updateDeleteSelectedBtn();
  });
  sapTbody?.addEventListener('change', (e) => {
    if (e.target.classList.contains('sap-row-select')) updateDeleteSelectedBtn();
  });

  btnSapSearch?.addEventListener('click', () => loadSapItems(0));
  btnSapPrev?.addEventListener('click', () => { if (sapSkip > 0) loadSapItems(Math.max(0, sapSkip - sapTop)); });
  btnSapNext?.addEventListener('click', () => { if (sapHasMore) loadSapItems(sapSkip + sapTop); });
  btnSapSync?.addEventListener('click', syncAllFromSap);
  document.getElementById('btn-save-sap-edit')?.addEventListener('click', saveSapEdit);

  document.getElementById('tab-sap-btn')?.addEventListener('shown.bs.tab', () => {
    if (sapTbody && sapTbody.querySelector('td[colspan]')) loadSapItems(0);
  });

  if (document.getElementById('tab-sap')?.classList.contains('active') && sapTbody) {
    loadSapItems(0);
  }
})();
