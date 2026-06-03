/**
 * Coating types list page.
 */
(function () {
  const table = document.getElementById('coatings-table');
  if (!table) return;

  async function parseJson(res) {
    try {
      return await res.json();
    } catch {
      return { error: res.ok ? 'Invalid server response' : 'Server error (' + res.status + ')' };
    }
  }

  function openEditFromRow(btn) {
    const row = btn.closest('tr');
    const id = row.getAttribute('data-cid');
    document.getElementById('edit_coating_id').value = id;
    document.getElementById('edit_coating_code').value = row.querySelector('.col-code').innerText.trim();
    document.getElementById('edit_coating_name').value = row.querySelector('.col-name').innerText.trim();
    document.getElementById('edit_coating_active').checked = !!row.querySelector('.col-active .status-pill.active');
    document.getElementById('edit_coating_error').style.display = 'none';
    new bootstrap.Modal(document.getElementById('editCoatingModal')).show();
  }

  async function submitEdit() {
    const id = document.getElementById('edit_coating_id').value;
    const code = document.getElementById('edit_coating_code').value.trim();
    const name = document.getElementById('edit_coating_name').value.trim();
    const active = document.getElementById('edit_coating_active').checked;
    const errEl = document.getElementById('edit_coating_error');
    if (!code || !name) {
      errEl.innerText = 'Code and name required';
      errEl.style.display = 'block';
      return;
    }
    try {
      const res = await fetch('/coating-types/ajax_update', {
        method: 'POST',
        headers: typeof csrfHeaders === 'function' ? csrfHeaders() : { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, code, name, is_active: active }),
      });
      const body = await parseJson(res);
      if (res.status === 200) {
        const row = document.querySelector(`tr[data-cid='${id}']`);
        row.querySelector('.col-code').innerText = body.code;
        row.querySelector('.col-name').innerText = body.name;
        row.querySelector('.col-active').innerHTML = body.is_active
          ? '<span class="status-pill active"><i class="bi bi-check-circle-fill"></i> Active</span>'
          : '<span class="status-pill inactive">Inactive</span>';
        bootstrap.Modal.getInstance(document.getElementById('editCoatingModal')).hide();
      } else {
        errEl.innerText = body.error || 'Update failed';
        errEl.style.display = 'block';
      }
    } catch {
      errEl.innerText = 'Request failed';
      errEl.style.display = 'block';
    }
  }

  async function deleteCoating(id) {
    if (!confirm('Delete this coating type? This cannot be undone.')) return;
    try {
      const res = await fetch('/coating-types/ajax_delete', {
        method: 'POST',
        headers: typeof csrfHeaders === 'function' ? csrfHeaders() : { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: Number(id) }),
      });
      const body = await parseJson(res);
      if (res.status === 200) {
        document.querySelector(`tr[data-cid='${id}']`)?.remove();
        if (typeof showToast === 'function') showToast('Coating deleted', 'success');
      } else {
        alert(body.error || 'Delete failed');
      }
    } catch {
      alert('Request failed — check that the server is running.');
    }
  }

  async function deleteByIdentifier() {
    const idf = (document.getElementById('coating_delete_identifier') || document.getElementById('delete_identifier'))?.value.trim();
    if (!idf) {
      alert('Enter full code or name to delete');
      return;
    }
    if (!confirm(`Delete entry matching '${idf}'? This cannot be undone.`)) return;
    try {
      const res = await fetch('/coating-types/ajax_delete', {
        method: 'POST',
        headers: typeof csrfHeaders === 'function' ? csrfHeaders() : { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier: idf }),
      });
      const body = await parseJson(res);
      if (res.status === 200) {
        document.querySelector(`tr[data-cid='${body.deleted_id}']`)?.remove();
        if (typeof showToast === 'function') showToast('Coating deleted', 'success');
      } else {
        alert(body.error || 'Delete failed');
      }
    } catch {
      alert('Request failed — check that the server is running.');
    }
  }

  table.addEventListener('click', (e) => {
    const editBtn = e.target.closest('.btn-edit-coating');
    if (editBtn) {
      e.preventDefault();
      openEditFromRow(editBtn);
      return;
    }
    const delBtn = e.target.closest('.btn-delete-coating');
    if (delBtn) {
      e.preventDefault();
      const id = delBtn.closest('tr')?.getAttribute('data-cid');
      if (id) deleteCoating(id);
    }
  });

  (document.getElementById('btn-coating-delete-by-identifier') || document.getElementById('btn-delete-by-identifier'))
    ?.addEventListener('click', deleteByIdentifier);
  document.getElementById('btn-save-coating')?.addEventListener('click', submitEdit);
})();
