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
