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

/**
 * Normalize thickness (micron) for <input type="number"> — avoids 13 → 12.99 float drift.
 * Rounds to 3 decimal places; whole numbers display without decimals (13 not 13.000).
 */
function normalizeThicknessMicInput(el) {
  if (!el || el.value === '' || el.value == null) return NaN;
  const raw = String(el.value).trim().replace(',', '.');
  const n = parseFloat(raw, 10);
  if (Number.isNaN(n) || n < 0) return NaN;
  const rounded = Math.round(n * 1000) / 1000;
  if (rounded === Math.floor(rounded)) {
    el.value = String(Math.floor(rounded));
  } else {
    el.value = String(rounded);
  }
  return rounded;
}

/** @deprecated use normalizeThicknessMicInput */
function normalizeThicknessMmInput(el) {
  return normalizeThicknessMicInput(el);
}

window.UNIT1_UNITS = window.UNIT1_UNITS || {
  thickness: 'mic',
  thicknessSuffix: 'MIC',
  length: 'mtr',
  width: 'mm',
};

function unit1FgThicknessSuffix() {
  return (window.UNIT1_UNITS && window.UNIT1_UNITS.thicknessSuffix) || 'MIC';
}

function bindThicknessInputs(root) {
  const scope = root || document;
  scope.querySelectorAll('input[data-thickness-mic], input[data-thickness-mm]').forEach(function (el) {
    if (el.dataset.thicknessBound === '1') return;
    el.dataset.thicknessBound = '1';
    el.addEventListener('blur', function () {
      normalizeThicknessMicInput(el);
    });
    el.addEventListener('change', function () {
      normalizeThicknessMicInput(el);
    });
  });
}

/**
 * Bootstrap modals inside .app-content sit below the body-level backdrop (clicks blocked).
 * Move all modals to document.body so backdrop + dialog share the same stacking context.
 */
function relocateBootstrapModals() {
  document.querySelectorAll('.modal.fade').forEach(function (el) {
    if (el.parentElement !== document.body) {
      document.body.appendChild(el);
    }
  });
}

function cleanupStuckModalState() {
  document.querySelectorAll('.modal-backdrop').forEach(function (el) {
    el.remove();
  });
  document.body.classList.remove('modal-open');
  document.body.style.removeProperty('overflow');
  document.body.style.removeProperty('padding-right');
  document.querySelectorAll('.modal.show').forEach(function (el) {
    el.classList.remove('show');
    el.style.removeProperty('display');
    el.setAttribute('aria-hidden', 'true');
  });
}

document.addEventListener('hidden.bs.modal', function () {
  setTimeout(function () {
    if (!document.querySelector('.modal.show')) {
      document.querySelectorAll('.modal-backdrop').forEach(function (el) {
        el.remove();
      });
      document.body.classList.remove('modal-open');
      document.body.style.removeProperty('overflow');
      document.body.style.removeProperty('padding-right');
    }
  }, 0);
});

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && document.querySelectorAll('.modal.show').length === 0) {
    cleanupStuckModalState();
  }
});

document.addEventListener('DOMContentLoaded', function () {
  relocateBootstrapModals();
  bindThicknessInputs(document);
  if (document.querySelector('.modal-backdrop') && !document.querySelector('.modal.show')) {
    cleanupStuckModalState();
  }
});
