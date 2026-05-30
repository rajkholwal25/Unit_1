/**
 * Material types list page — no inline handlers (avoids editor false positives).
 */
(function () {
  const table = document.getElementById('materials-table');
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
    const id = row.getAttribute('data-mid');
    document.getElementById('edit_material_id').value = id;
    document.getElementById('edit_material_code').value = row.querySelector('.col-code').innerText.trim();
    document.getElementById('edit_material_name').value = row.querySelector('.col-name').innerText.trim();
    document.getElementById('edit_material_active').checked = !!row.querySelector('.col-active .status-pill.active');
    document.getElementById('edit_material_error').style.display = 'none';
    new bootstrap.Modal(document.getElementById('editMaterialModal')).show();
  }

  async function submitEdit() {
    const id = document.getElementById('edit_material_id').value;
    const code = document.getElementById('edit_material_code').value.trim();
    const name = document.getElementById('edit_material_name').value.trim();
    const active = document.getElementById('edit_material_active').checked;
    const errEl = document.getElementById('edit_material_error');
    if (!code || !name) {
      errEl.innerText = 'Code and name required';
      errEl.style.display = 'block';
      return;
    }
    try {
      const res = await fetch('/material-types/ajax_update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, code, name, is_active: active }),
      });
      const body = await parseJson(res);
      if (res.status === 200) {
        const row = document.querySelector(`tr[data-mid='${id}']`);
        row.querySelector('.col-code').innerText = body.code;
        row.querySelector('.col-name').innerText = body.name;
        row.querySelector('.col-active').innerHTML = body.is_active
          ? '<span class="status-pill active"><i class="bi bi-check-circle-fill"></i> Active</span>'
          : '<span class="status-pill inactive">Inactive</span>';
        bootstrap.Modal.getInstance(document.getElementById('editMaterialModal')).hide();
      } else {
        errEl.innerText = body.error || 'Update failed';
        errEl.style.display = 'block';
      }
    } catch {
      errEl.innerText = 'Request failed';
      errEl.style.display = 'block';
    }
  }

  async function deleteMaterial(id) {
    if (!confirm('Delete this material? This cannot be undone.')) return;
    try {
      const res = await fetch('/material-types/ajax_delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: Number(id) }),
      });
      const body = await parseJson(res);
      if (res.status === 200) {
        document.querySelector(`tr[data-mid='${id}']`)?.remove();
        if (typeof showToast === 'function') showToast('Material deleted', 'success');
      } else {
        alert(body.error || 'Delete failed');
      }
    } catch {
      alert('Request failed — check that the server is running.');
    }
  }

  async function deleteByIdentifier() {
    const idf = document.getElementById('delete_identifier').value.trim();
    if (!idf) {
      alert('Enter full code or name to delete');
      return;
    }
    if (!confirm(`Delete entry matching '${idf}'? This cannot be undone.`)) return;
    try {
      const res = await fetch('/material-types/ajax_delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier: idf }),
      });
      const body = await parseJson(res);
      if (res.status === 200) {
        document.querySelector(`tr[data-mid='${body.deleted_id}']`)?.remove();
        if (typeof showToast === 'function') showToast('Material deleted', 'success');
      } else {
        alert(body.error || 'Delete failed');
      }
    } catch {
      alert('Request failed — check that the server is running.');
    }
  }

  table.addEventListener('click', (e) => {
    const editBtn = e.target.closest('.btn-edit-material');
    if (editBtn) {
      e.preventDefault();
      openEditFromRow(editBtn);
      return;
    }
    const delBtn = e.target.closest('.btn-delete-material');
    if (delBtn) {
      e.preventDefault();
      const id = delBtn.closest('tr')?.getAttribute('data-mid');
      if (id) deleteMaterial(id);
    }
  });

  document.getElementById('btn-delete-by-identifier')?.addEventListener('click', deleteByIdentifier);
  document.getElementById('btn-save-material')?.addEventListener('click', submitEdit);
})();
