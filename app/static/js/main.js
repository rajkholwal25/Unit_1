/** CSRF header for JSON fetch (Flask-WTF reads X-CSRFToken). */
function csrfHeaders(extra) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, extra || {});
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta) {
    headers['X-CSRFToken'] = meta.getAttribute('content');
  }
  return headers;
}

/** Parse JSON from fetch; handles 500 HTML error pages gracefully. */
async function parseJsonResponse(res) {
  try {
    return await res.json();
  } catch {
    return { error: res.ok ? 'Invalid server response' : 'Server error (' + res.status + ')' };
  }
}

/** Shared UI helpers */
function showToast(message, type) {
  type = type || 'info';
  const div = document.createElement('div');
  div.className = `alert alert-${type} position-fixed shadow`;
  div.style.cssText = 'right:20px;top:20px;z-index:2000;min-width:220px;';
  div.textContent = message;
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 3000);
}

document.addEventListener('DOMContentLoaded', function () {
  console.log('BOM Automation UI ready');
});
