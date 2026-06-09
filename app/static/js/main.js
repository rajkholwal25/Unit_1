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
 * Format a decimal for inputs without float drift (4.6 stays 4.6, 13 stays 13).
 * Pure string math — never uses Number()/toFixed() so binary float cannot rewrite the value.
 */
function formatDecimalInputValue(raw, maxDecimals) {
  const s = String(raw == null ? '' : raw).trim().replace(',', '.');
  if (s === '' || s === '.' || s === '-' || s === '-.') return '';
  const m = s.match(/^(-?)(\d*)(?:\.(\d*))?$/);
  if (!m) return s;
  const dp = Math.max(0, Math.min(6, parseInt(maxDecimals, 10) || 3));
  const sign = m[1] || '';
  let intPart = m[2] === '' ? '0' : m[2];
  let frac = m[3] || '';
  if (frac.length > dp) frac = frac.slice(0, dp);
  frac = frac.replace(/0+$/, '');
  if (intPart.length > 1) intPart = intPart.replace(/^0+/, '') || '0';
  let out = sign + intPart;
  if (frac.length) out += '.' + frac;
  return out;
}

function normalizeDecimalInput(el, maxDecimals) {
  if (!el || el.value === '' || el.value == null) return NaN;
  const formatted = formatDecimalInputValue(el.value, maxDecimals);
  if (formatted === '') return NaN;
  el.value = formatted;
  return Number(formatted);
}

/**
 * Normalize thickness (micron) for <input type="number">.
 */
function normalizeThicknessMicInput(el) {
  return normalizeDecimalInput(el, 3);
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
    function onNorm() {
      normalizeThicknessMicInput(el);
    }
    el.addEventListener('blur', onNorm);
    el.addEventListener('change', onNorm);
  });
}

function bindDecimalInputs(root) {
  const scope = root || document;
  scope.querySelectorAll('input[data-decimal-input]').forEach(function (el) {
    if (el.dataset.decimalBound === '1') return;
    el.dataset.decimalBound = '1';
    const dp = parseInt(el.getAttribute('data-decimal-places') || '3', 10);
    function onNorm() {
      normalizeDecimalInput(el, dp);
    }
    if (el.value) onNorm();
    el.addEventListener('blur', onNorm);
    el.addEventListener('change', onNorm);
  });
}

function parseDecimalField(raw, maxDecimals) {
  const s = formatDecimalInputValue(raw, maxDecimals);
  if (s === '') return NaN;
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
}

/** Display a numeric value in a decimal text input without float drift. */
function formatDecimalForInput(num, maxDecimals) {
  if (num == null || num === '') return '';
  if (typeof num === 'number' && !Number.isFinite(num)) return '';
  return formatDecimalInputValue(String(num), maxDecimals);
}

function setDecimalInputValue(el, num, maxDecimals) {
  if (!el) return;
  const dp = maxDecimals == null ? 3 : maxDecimals;
  el.value = formatDecimalForInput(num, dp);
}

window.formatDecimalInputValue = formatDecimalInputValue;
window.normalizeDecimalInput = normalizeDecimalInput;
window.parseDecimalField = parseDecimalField;
window.formatDecimalForInput = formatDecimalForInput;
window.setDecimalInputValue = setDecimalInputValue;
window.bindDecimalInputs = bindDecimalInputs;

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
  bindDecimalInputs(document);
  if (document.querySelector('.modal-backdrop') && !document.querySelector('.modal.show')) {
    cleanupStuckModalState();
  }
});
