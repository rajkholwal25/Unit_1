/**
 * Generated item codes list — copy and delete.
 */
(function () {
  const table = document.getElementById('generated-items-table');
  if (!table) return;

  function copyCode(code) {
    navigator.clipboard.writeText(code).then(() => {
      if (typeof showToast === 'function') showToast('Copied: ' + code, 'success');
      else alert('Copied: ' + code);
    }).catch(() => alert('Copy failed'));
  }

  async function deleteItem(id, code) {
    if (!confirm(`Delete generated item "${code}" and all its process codes?\n\nThis cannot be undone.`)) {
      return;
    }
    try {
      const res = await fetch('/item-codes/ajax_delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: Number(id) }),
      });
      const body = typeof parseJsonResponse === 'function'
        ? await parseJsonResponse(res)
        : await res.json();
      if (res.status === 200) {
        document.querySelector(`tr[data-fg-id='${id}']`)?.remove();
        if (!table.querySelector('tbody tr[data-fg-id]')) {
          table.querySelector('tbody').innerHTML =
            '<tr><td colspan="7" class="text-center text-muted py-4">No generated items saved yet.</td></tr>';
        }
        if (typeof showToast === 'function') showToast('Item deleted', 'success');
      } else {
        alert(body.error || 'Delete failed');
      }
    } catch {
      alert('Request failed — check that the server is running.');
    }
  }

  table.addEventListener('click', (e) => {
    const copyBtn = e.target.closest('.btn-copy-item');
    if (copyBtn) {
      e.preventDefault();
      copyCode(copyBtn.closest('tr').getAttribute('data-code'));
      return;
    }
    const delBtn = e.target.closest('.btn-delete-item');
    if (delBtn) {
      e.preventDefault();
      const row = delBtn.closest('tr');
      deleteItem(row.getAttribute('data-fg-id'), row.getAttribute('data-code'));
    }
  });
})();
