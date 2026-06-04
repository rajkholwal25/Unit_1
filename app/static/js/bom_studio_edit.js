/**
 * BOM studio on edit_bom_spec: SAP item search, add/remove required rows, serialize to bom_payload_json.
 * DOM contract matches job_cards/form.html (mergeStateFromDom / BOM submit block).
 */
(function () {
  const form = document.getElementById('editBomMainForm');
  const bomContainer = document.getElementById('bomBlocksContainer');
  const bomHidden = document.getElementById('bom_payload_json');
  if (!form || !bomContainer || !bomHidden) return;

  const itemsSearchUrl = form.getAttribute('data-items-search-url') || '';
  const UNIT1_DEFAULT_UOM = (form.getAttribute('data-unit1-uom') || 'KGS').trim() || 'KGS';

  /** Mirrors job_cards/form.html ``extractFgNum`` for co-product / linkage rows. */
  function extractFgNum(str) {
    if (!str) return 'FG';
    const m = String(str).match(/(FG\d+)/i);
    return m ? m[1].toUpperCase() : String(str).trim();
  }

  /** SAP item code from a BOM required row (before em dash in display). */
  function linkCodeFromRow(rowEl) {
    const inp = rowEl.querySelector('.bom-itemcode-input');
    if (!inp) return '';
    const raw = String(inp.value || '').split('\u2014')[0].trim();
    return raw.toUpperCase();
  }

  /** Mirrors ``bom_edit_payload.is_die_split_process`` (code + display name). */
  function isSplitSection(sec) {
    if (!sec) return false;
    const code = (sec.getAttribute('data-process-code') || '').trim().toUpperCase();
    const title = (sec.getAttribute('data-process-name') || '').trim();
    const dieCodes = [
      'CV-DIE', 'DIE', 'DIECUT', 'DIECUTTING', 'DIE-CUT', 'DIE-TRY', 'DIE-TRAY', 'EMB+P',
    ];
    if (dieCodes.indexOf(code) >= 0) return true;
    const n = title.toLowerCase();
    if (n.indexOf('diecut') >= 0) return true;
    if (n.indexOf('die') >= 0 && n.indexOf('cut') >= 0) return true;
    return false;
  }

  function isFgSection(sec) {
    if (!sec) return false;
    const code = (sec.getAttribute('data-process-code') || '').trim().toUpperCase();
    const titleU = (sec.getAttribute('data-process-name') || '').trim().toUpperCase();
    if (code === 'FG' || code === 'PK-PACK') return true;
    if (titleU === 'FG') return true;
    return false;
  }

  function headerOutputCodeFromCard(card) {
    const nameInp = card.querySelector('.bom-item-name');
    if (!nameInp) return '';
    const raw = String(nameInp.value || '').split('\u2014')[0].trim();
    return raw.toUpperCase();
  }

  function findPrevExtraRowByLinkCode(pc, wantU) {
    if (!wantU || !pc) return null;
    let found = null;
    pc.querySelectorAll('.bom-extra-wrap .row').forEach(function (r) {
      if (found) return;
      const c = linkCodeFromRow(r);
      if (c && c === wantU) found = r;
    });
    return found;
  }

  /**
   * SAP item code for the **immediate** previous process section's output for this card index.
   * Matches server-side ``persist_bom_payload_block`` / ``last_step_by_card`` wiring so linkage rows
   * do not keep a stale code after inserting a step (e.g. Embossing between Printing and Diecutting).
   */
  function immediatePrevSectionOutputItemCode(steps, curStepIdx, cardIdx) {
    if (curStepIdx < 1) return '';
    const prevSec = steps[curStepIdx - 1];
    const prevCards = prevSec.querySelectorAll('.bom-process-card');
    if (!prevCards.length) return '';
    if (prevCards.length === 1) {
      return headerOutputCodeFromCard(prevCards[0]) || '';
    }
    const keyed = prevSec.querySelector(
      '.bom-process-card[data-card-idx="' + cardIdx + '"]'
    );
    if (keyed) {
      const h = headerOutputCodeFromCard(keyed);
      if (h) return h;
    }
    return headerOutputCodeFromCard(prevCards[0]) || '';
  }

  /**
   * Find planned qty / UoM / WH for a required line that consumes ``wantLink`` (e.g. die semi MON-DIE).
   * Walks **upstream** process sections — not only the DOM neighbour — so PST can still follow die
   * after LAM/mounting. Prefers a card **header** whose item code matches ``wantLink`` (live planned
   * qty) over a same-code extra row, so a stale positive duplicate line does not block updates.
   */
  function resolveUpstreamLinkedOutput(steps, curStepIdx, wantLink, cardIdx, ln) {
    if (!wantLink || curStepIdx < 1) return null;
    for (let pi = curStepIdx - 1; pi >= 0; pi--) {
      const prevSec = steps[pi];
      const prevCards = prevSec.querySelectorAll('.bom-process-card');
      const isPrevSplit = isSplitSection(prevSec);

      function pullFromRow(row) {
        const qIn = row.querySelector('.bom-item-qty');
        const qn = parseFloat(qIn && qIn.value);
        const qAdj = !isNaN(qn) ? (qn < 0 ? Math.abs(qn) : qn) : 0;
        const um = row.querySelector('.bom-item-uom');
        const wh = row.querySelector('.bom-item-wh');
        return {
          qty: String(qAdj),
          uom: um ? um.value : null,
          wh: wh ? wh.value : null,
          itemCode: linkCodeFromRow(row) || null,
        };
      }

      function pullFromHeader(prevCard) {
        const pq = prevCard.querySelector('.bom-planned-qty');
        const pu = prevCard.querySelector('.bom-uom');
        const pw = prevCard.querySelector('.bom-warehouse');
        return {
          qty: pq ? pq.value : null,
          uom: pu ? pu.value : null,
          wh: pw ? pw.value : null,
          itemCode: headerOutputCodeFromCard(prevCard) || null,
        };
      }

      function tryCard(prevCard) {
        const headerCode = headerOutputCodeFromCard(prevCard);
        if (headerCode === wantLink) {
          const h = pullFromHeader(prevCard);
          if (h.qty != null) return h;
        }
        let matched = findPrevExtraRowByLinkCode(prevCard, wantLink);
        if (!matched && cardIdx > 0 && isPrevSplit) {
          const itmPrefix = extractFgNum(ln.fg_code || '') + '-';
          matched =
            Array.prototype.slice
              .call(prevCard.querySelectorAll('.bom-extra-wrap .row'))
              .find(function (r) {
                const val = r.querySelector('.bom-itemcode-input')
                  ? r.querySelector('.bom-itemcode-input').value || ''
                  : '';
                return itmPrefix && val.indexOf(itmPrefix) === 0;
              }) || null;
        }
        if (matched) {
          return pullFromRow(matched);
        }
        return null;
      }

      if (prevCards.length === 1) {
        const hit = tryCard(prevCards[0]);
        if (hit && hit.qty != null) return hit;
      } else {
        const keyed = prevSec.querySelector(
          '.bom-process-card[data-card-idx="' + cardIdx + '"]'
        );
        if (keyed) {
          const hit = tryCard(keyed);
          if (hit && hit.qty != null) return hit;
        }
        let hitAny = null;
        Array.prototype.forEach.call(prevCards, function (pc) {
          if (hitAny) return;
          const hit = tryCard(pc);
          if (hit && hit.qty != null) hitAny = hit;
        });
        if (hitAny) return hitAny;
      }
    }
    return null;
  }

  /** Header FG rows in table order — same index as BOM ``data-card-idx`` for this detail line. */
  function fgDispatchLinesFromEditForm() {
    const out = [];
    const tbody = form.querySelector('.table-responsive tbody');
    if (tbody) {
      tbody.querySelectorAll('tr').forEach(function (tr) {
        const fgCode = (tr.getAttribute('data-fg-code') || '').trim();
        const inpUps = tr.querySelector('input[name$="_ups"]');
        const inpDq = tr.querySelector('input[name$="_dispatch_qty"]');
        if (!inpUps) return;
        out.push({
          fg_code: fgCode,
          ups: parseFloat(inpUps.value) || 1,
          quantity: parseFloat(inpDq && inpDq.value) || 0,
        });
      });
    }
    if (!out.length) {
      form.querySelectorAll('input[name^="fg_"][name$="_ups"]').forEach(function (inp) {
        const m = inp.name.match(/^fg_(\d+)_ups$/);
        if (!m) return;
        const id = m[1];
        const dq = form.querySelector('input[name="fg_' + id + '_dispatch_qty"]');
        out.push({
          fg_code: '',
          ups: parseFloat(inp.value) || 1,
          quantity: parseFloat(dq && dq.value) || 0,
        });
      });
    }
    return out;
  }

  /**
   * Unit 1: gross planned qty in KGS (max dispatch kg + wastage kg).
   */
  function rmKgFromFgAndWastage(netKg, wastageKg) {
    return Math.ceil((parseFloat(netKg) || 0) + (parseFloat(wastageKg) || 0));
  }

  function countConvertingStepsEdit(steps) {
    let n = 0;
    steps.forEach(function (sec) {
      if (!isFgSection(sec)) n++;
    });
    return n;
  }

  function plannedKgForConvertStep(rmKg) {
    return Math.ceil(parseFloat(rmKg) || 0);
  }

  function syncEditBomFromFg() {
    const wsInp = document.getElementById('detail_wastage_sheets_input');
    const wastageKg = parseFloat(wsInp && wsInp.value) || 0;
    const lines = fgDispatchLinesFromEditForm();

    let maxKg = 0;
    lines.forEach(function (l) {
      const q = parseFloat(l.quantity) || 0;
      if (q > maxKg) maxKg = q;
    });
    const block = bomContainer.querySelector('.bom-line-block');
    if (!block) return;

    const steps = Array.prototype.slice.call(block.querySelectorAll('.bom-sections-inner > .border'));
    const totalKg = rmKgFromFgAndWastage(maxKg, wastageKg);
    let gross = totalKg < 1 ? 1 : totalKg;

    const hid = document.getElementById('detail_total_sheets_hidden');
    if (hid) hid.value = String(gross);

    let convStepIndex = 0;
    let hasReachedSplit = false;

    steps.forEach(function (sec, stepIdx) {
      const isSplitStep = isSplitSection(sec);
      const isFg = isFgSection(sec);
      const convIdx = isFg ? -1 : convStepIndex++;
      if (isSplitStep) hasReachedSplit = true;

      sec.querySelectorAll('.bom-process-card').forEach(function (card) {
        let cardIdx = parseInt(card.getAttribute('data-card-idx'), 10);
        if (isNaN(cardIdx)) cardIdx = 0;
        const qtyEl = card.querySelector('.bom-planned-qty');
        const uomEl = card.querySelector('.bom-uom');
        const ln = lines[cardIdx] || {};
        const lineKg = parseFloat(ln.quantity) || 0;

        let headerQty;
        if (isFg) {
          headerQty = lineKg > 0 ? lineKg : maxKg;
        } else if (convIdx >= 0) {
          headerQty = plannedKgForConvertStep(gross);
        } else {
          headerQty = lineKg > 0 ? lineKg : gross;
        }
        let headerUom = UNIT1_DEFAULT_UOM;

        if (isSplitStep && cardIdx === 0) {
          card.querySelectorAll('.bom-extra-wrap .row').forEach(function (ext) {
            const isManualExtra =
              ext.getAttribute('data-is-prev-output') !== 'true' &&
              ext.getAttribute('data-bom-qty-driver') !== 'true';
            const qInp = ext.querySelector('.bom-item-qty');
            if (isManualExtra && qInp && parseFloat(qInp.value) < 0) {
              const itmCode = ext.querySelector('.bom-itemcode-input')
                ? ext.querySelector('.bom-itemcode-input').value || ''
                : '';
              const match = lines.find(function (l) {
                const fgNum = extractFgNum(l.fg_code || '');
                return fgNum && itmCode.indexOf(fgNum + '-') === 0;
              });
              if (match) {
                const matchKg = parseFloat(match.quantity) || 0;
                qInp.value = -1 * Math.ceil(matchKg > 0 ? matchKg : gross);
              }
            }
          });
        }

        if (qtyEl && shouldAutoWrite(qtyEl)) qtyEl.value = headerQty;
        if (uomEl && shouldAutoWrite(uomEl)) uomEl.value = headerUom;

        card.querySelectorAll('.bom-extra-wrap .row').forEach(function (ext) {
          const isPrevLink = ext.getAttribute('data-is-prev-output') === 'true';
          const isRootDriver = ext.getAttribute('data-bom-qty-driver') === 'true';
          const wantLink = linkCodeFromRow(ext);
          const qInput = ext.querySelector('.bom-item-qty');
          const uInput = ext.querySelector('.bom-item-uom');
          const whInput = ext.querySelector('.bom-item-wh');

          function syncLinkageItemCode(newCodeRaw) {
            const raw = String(newCodeRaw || '').trim();
            if (!raw) return;
            const codeOnly = raw.split('\u2014')[0].trim();
            if (!codeOnly) return;
            const itemInp = ext.querySelector('.bom-itemcode-input');
            if (!itemInp) return;
            const cur = String(itemInp.value || '').split('\u2014')[0].trim();
            if (cur.toUpperCase() === codeOnly.toUpperCase()) return;
            const lockPrev = ext.getAttribute('data-is-prev-output') === 'true';
            if (lockPrev) itemInp.removeAttribute('readonly');
            itemInp.value = codeOnly;
            if (lockPrev) itemInp.setAttribute('readonly', 'readonly');
          }

          if (isRootDriver && stepIdx === 0) {
            if (qInput && shouldAutoWrite(qInput)) qInput.value = gross;
            if (uInput && shouldAutoWrite(uInput)) uInput.value = UNIT1_DEFAULT_UOM;
          } else if (isPrevLink) {
            if (stepIdx > 0) {
              let prevOutputQty = null;
              let prevOutputUom = null;
              let prevOutputWh = null;

              const prevSec = steps[stepIdx - 1];
              const isPrevSplit = isSplitSection(prevSec);
              const prevCards = prevSec.querySelectorAll('.bom-process-card');

              if (prevCards.length === 1) {
                const prevCard = prevCards[0];
                let matched = findPrevExtraRowByLinkCode(prevCard, wantLink);
                if (!matched && cardIdx > 0 && isPrevSplit) {
                  const itmPrefix = extractFgNum(ln.fg_code || '') + '-';
                  matched = Array.prototype.slice
                    .call(prevCard.querySelectorAll('.bom-extra-wrap .row'))
                    .find(function (r) {
                      const val = r.querySelector('.bom-itemcode-input')
                        ? r.querySelector('.bom-itemcode-input').value || ''
                        : '';
                      return itmPrefix && val.indexOf(itmPrefix) === 0;
                    }) || null;
                }
                const headerFirst = headerOutputCodeFromCard(prevCard) === wantLink;
                if (headerFirst) {
                  const pq = prevCard.querySelector('.bom-planned-qty');
                  const pu = prevCard.querySelector('.bom-uom');
                  const pw = prevCard.querySelector('.bom-warehouse');
                  prevOutputQty = pq ? pq.value : null;
                  prevOutputUom = pu ? pu.value : null;
                  prevOutputWh = pw ? pw.value : null;
                } else if (matched) {
                  const qn = parseFloat(matched.querySelector('.bom-item-qty').value);
                  const qAdj = !isNaN(qn) ? (qn < 0 ? Math.abs(qn) : qn) : 0;
                  prevOutputQty = String(qAdj);
                  const um = matched.querySelector('.bom-item-uom');
                  const wh = matched.querySelector('.bom-item-wh');
                  prevOutputUom = um ? um.value : null;
                  prevOutputWh = wh ? wh.value : null;
                } else {
                  const pq = prevCard.querySelector('.bom-planned-qty');
                  const pu = prevCard.querySelector('.bom-uom');
                  const pw = prevCard.querySelector('.bom-warehouse');
                  prevOutputQty = pq ? pq.value : null;
                  prevOutputUom = pu ? pu.value : null;
                  prevOutputWh = pw ? pw.value : null;
                }
              } else {
                let matched = null;
                Array.prototype.forEach.call(prevCards, function (pc) {
                  if (matched) return;
                  const hit = findPrevExtraRowByLinkCode(pc, wantLink);
                  if (hit) matched = { row: hit, card: pc };
                });
                const keyedCard = prevSec.querySelector(
                  '.bom-process-card[data-card-idx="' + cardIdx + '"]'
                );
                const headerKeyed = keyedCard && headerOutputCodeFromCard(keyedCard) === wantLink;
                if (headerKeyed && keyedCard) {
                  const pq = keyedCard.querySelector('.bom-planned-qty');
                  const pu = keyedCard.querySelector('.bom-uom');
                  const pw = keyedCard.querySelector('.bom-warehouse');
                  prevOutputQty = pq ? pq.value : null;
                  prevOutputUom = pu ? pu.value : null;
                  prevOutputWh = pw ? pw.value : null;
                } else if (matched) {
                  const qn = parseFloat(matched.row.querySelector('.bom-item-qty').value);
                  const qAdj = !isNaN(qn) ? (qn < 0 ? Math.abs(qn) : qn) : 0;
                  prevOutputQty = String(qAdj);
                  const um = matched.row.querySelector('.bom-item-uom');
                  const wh = matched.row.querySelector('.bom-item-wh');
                  prevOutputUom = um ? um.value : null;
                  prevOutputWh = wh ? wh.value : null;
                } else if (keyedCard) {
                  const pq = keyedCard.querySelector('.bom-planned-qty');
                  const pu = keyedCard.querySelector('.bom-uom');
                  const pw = keyedCard.querySelector('.bom-warehouse');
                  prevOutputQty = pq ? pq.value : null;
                  prevOutputUom = pu ? pu.value : null;
                  prevOutputWh = pw ? pw.value : null;
                }
              }

              const qPeek = parseFloat(qInput && qInput.value);
              const isDieCoproductRow = !isNaN(qPeek) && qPeek < 0;
              if (!isDieCoproductRow) {
                syncLinkageItemCode(immediatePrevSectionOutputItemCode(steps, stepIdx, cardIdx));
              }

              if (prevOutputQty != null) {
                if (qInput && shouldAutoWrite(qInput)) qInput.value = prevOutputQty;
                if (uInput && shouldAutoWrite(uInput)) uInput.value = prevOutputUom;
                if (whInput && shouldAutoWrite(whInput) && ext.getAttribute('data-force-ohjw') !== 'true') {
                  whInput.value = prevOutputWh;
                }
                if (isSplitStep && uInput) {
                  if (shouldAutoWrite(uInput)) uInput.value = UNIT1_DEFAULT_UOM;
                }
              }
            } else if (stepIdx === 0) {
              if (qInput && shouldAutoWrite(qInput)) qInput.value = gross;
              if (uInput && shouldAutoWrite(uInput)) uInput.value = UNIT1_DEFAULT_UOM;
            }
          }
          const qEl = ext.querySelector('.bom-item-qty');
          const qVal = parseFloat(qEl && qEl.value) || 0;
          if (qVal < 0) {
            const uIn = ext.querySelector('.bom-item-uom');
            if (uIn) uIn.value = UNIT1_DEFAULT_UOM;
          }
        });
      });
    });
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  /** Required-items region: the .border-top that contains this card's "+ Add" button (avoids wrong .border-top / duplicate wraps). */
  function requiredItemsTop(card) {
    if (!card) return null;
    const addBtn = card.querySelector('.bom-add-extra');
    return (addBtn && addBtn.closest('.border-top')) || card.querySelector('.border-top');
  }

  function extraWrapForCard(card) {
    const top = requiredItemsTop(card);
    return top ? top.querySelector('.bom-extra-wrap') : card.querySelector('.bom-extra-wrap');
  }

  let itemTmo;
  function fetchSapItemCodes(q, cb) {
    const t = (q || '').trim();
    if (t.length < 2 || !itemsSearchUrl) return;
    fetch(itemsSearchUrl + '?q=' + encodeURIComponent(t), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!Array.isArray(data) || data.error) return;
        window.__sapItemUomCache = window.__sapItemUomCache || {};
        let dl = document.getElementById('sapBomItemsDatalist');
        if (!dl) return;
        dl.innerHTML = '';
        data.forEach(function (it) {
          const o = document.createElement('option');
          const code = String(it.item_code || '').trim();
          const uom = String(it.uom || '').trim();
          if (code) window.__sapItemUomCache[code.toUpperCase()] = uom;
          o.value = code + (it.item_name ? (' \u2014 ' + it.item_name) : '');
          dl.appendChild(o);
        });
        cb(dl.id);
      })
      .catch(function () {});
  }

  function tryPrefillUom(el) {
    if (!el || !window.__sapItemUomCache) return;
    const raw = (el.value || '').split('\u2014')[0].trim().toUpperCase();
    const u = window.__sapItemUomCache[raw];
    if (!u) return;
    const row = el.closest('.row');
    const uomEl = row ? row.querySelector('.bom-item-uom') : null;
    if (!uomEl) return;
    if (uomEl.getAttribute('data-user-edited') === '1') return;
    if (!String(uomEl.value || '').trim()) uomEl.value = u;
  }

  function markUserEdited(el) {
    if (!el || !el.getAttribute) return;
    el.setAttribute('data-user-edited', '1');
  }

  function shouldAutoWrite(el) {
    if (!el) return false;
    return el.getAttribute('data-user-edited') !== '1';
  }

  // Preserve user edits after BOM generation (do not auto-overwrite).
  bomContainer.addEventListener('input', function (e) {
    const t = e.target;
    if (!t || !t.classList) return;
    if (
      t.classList.contains('bom-planned-qty') ||
      t.classList.contains('bom-uom') ||
      t.classList.contains('bom-item-qty') ||
      t.classList.contains('bom-item-uom')
    ) {
      markUserEdited(t);
    }
  }, true);
  bomContainer.addEventListener('change', function (e) {
    const t = e.target;
    if (!t || !t.classList) return;
    if (
      t.classList.contains('bom-warehouse') ||
      t.classList.contains('bom-item-wh')
    ) {
      markUserEdited(t);
    }
  }, true);

  function warehouseOptionsHtml(selected) {
    const opts = [
      ['II-RM', 'II-RM'], ['II-PSTR', 'II-PSTR'], ['II-PRI', 'II-PRI'], ['II-FOI', 'II-FOI'],
      ['II-PST', 'II-PST'], ['II-LAM', 'II-LAM'], ['II-DIE', 'II-DIE'], ['II-CORU', 'II-CORU'],
      ['II-COT', 'II-COT'], ['II-UV', 'II-UV'], ['II-MAN', 'II-MAN'], ['OHJW-U2', 'OHJW-U2'], ['OHJW-U1', 'OHJW-U1'],
      ['II-FG', 'II-FG'], ['II-EMB', 'II-EMB'],
    ];
    const sel = (selected || '').trim();
    const selNorm = sel === 'II-OHJW' ? 'OHJW-U2' : sel;
    return opts.map(function (pair) {
      const v = pair[0];
      const lab = pair[1];
      return '<option value="' + esc(v) + '"' + (v === selNorm ? ' selected' : '') + '>' + esc(lab) + '</option>';
    }).join('');
  }

  function addExtraRow(targetWrap, itemCode, qty, defaultWh, defaultUom) {
    const row = document.createElement('div');
    row.className = 'row g-2 mt-1 align-items-center';
    row.innerHTML =
      '<div class="col-md-4"><input type="text" class="form-control form-control-sm bom-itemcode-input" list="sapBomItemsDatalist" placeholder="Item code (SAP)" value="' +
      esc(itemCode || '') + '"></div>' +
      '<div class="col-md-3"><select class="form-select form-select-sm bom-item-wh">' +
      warehouseOptionsHtml(defaultWh || 'II-DIE') +
      '</select></div>' +
      '<div class="col-md-2"><input type="number" step="0.01" class="form-control form-control-sm bom-item-qty" placeholder="Qty" value="' +
      esc(qty || '') + '"></div>' +
      '<div class="col-md-2"><input type="text" class="form-control form-control-sm bom-item-uom" placeholder="UoM" value="' +
      esc(defaultUom || '') + '"></div>' +
      '<div class="col-md-1 text-end"><button type="button" class="btn btn-sm btn-outline-danger bom-rm-extra" title="Remove">\u00d7</button></div>';
    targetWrap.appendChild(row);
  }

  bomContainer.addEventListener('click', function (e) {
    const addBtn = e.target.closest('.bom-add-extra');
    if (addBtn) {
      e.preventDefault();
      const card = addBtn.closest('.bom-process-card');
      if (!card) return;
      const top = addBtn.closest('.border-top') || requiredItemsTop(card);
      let wrap = top ? top.querySelector('.bom-extra-wrap') : null;
      if (!wrap && top) {
        wrap = document.createElement('div');
        wrap.className = 'bom-extra-wrap';
        top.insertBefore(wrap, addBtn);
      }
      if (!wrap) wrap = extraWrapForCard(card);
      if (wrap) addExtraRow(wrap, '', '', 'FBD-EMB', UNIT1_DEFAULT_UOM);
      return;
    }
    const rmBtn = e.target.closest('.bom-rm-extra');
    if (rmBtn) {
      const r = rmBtn.closest('.row');
      const inp = r ? r.querySelector('.bom-itemcode-input') : null;
      if (inp && inp.readOnly) return;
      if (r) r.remove();
    }
  });

  bomContainer.addEventListener('input', function (e) {
    const el = e.target;
    if (!el.classList || !el.classList.contains('bom-itemcode-input')) return;
    window.clearTimeout(itemTmo);
    itemTmo = window.setTimeout(function () {
      fetchSapItemCodes(el.value, function () {
        tryPrefillUom(el);
      });
    }, 300);
  });
  bomContainer.addEventListener('change', function (e) {
    if (e.target && e.target.classList && e.target.classList.contains('bom-itemcode-input')) {
      tryPrefillUom(e.target);
    }
  });

  function serializeBomPayload() {
    const bomPayload = [];
    const lineBlocks = bomContainer.querySelectorAll('.bom-line-block');
    lineBlocks.forEach(function (lb) {
      const lineIndex = parseInt(lb.getAttribute('data-bom-line-index'), 10);
      if (isNaN(lineIndex)) return;
      const sectionsInner = lb.querySelector('.bom-sections-inner');
      const sectionEls = sectionsInner ? sectionsInner.querySelectorAll(':scope > .border') : [];
      if (sectionEls.length === 0) return;
      const sections = [];
      sectionEls.forEach(function (sec) {
        const processName = sec.getAttribute('data-process-name') || '';
        const processCode = sec.getAttribute('data-process-code') || '';
        const cards = [];
        sec.querySelectorAll('.bom-process-card').forEach(function (card) {
          const cardIdx = card.getAttribute('data-card-idx');
          const itemNameEl = card.querySelector('.bom-item-name');
          const qtyEl = card.querySelector('.bom-planned-qty');
          const whEl = card.querySelector('.bom-warehouse');
          const uomEl = card.querySelector('.bom-uom');
          const reqItems = [];
          const wrapEl = extraWrapForCard(card);
          if (wrapEl) {
            wrapEl.querySelectorAll('.row.g-2').forEach(function (r) {
              if (!r.querySelector('.bom-itemcode-input')) return;
              const itemEl = r.querySelector('.bom-itemcode-input');
              const whElRow = r.querySelector('.bom-item-wh');
              const qEl = r.querySelector('.bom-item-qty');
              const uomElRow = r.querySelector('.bom-item-uom');
              reqItems.push({
                sap_item_code: itemEl ? itemEl.value : '',
                warehouse: whElRow ? whElRow.value : '',
                warehouse_user_edited: whElRow ? whElRow.getAttribute('data-user-edited') === '1' : false,
                qty_per_job: qEl ? qEl.value : '',
                uom: uomElRow ? uomElRow.value : '',
              });
            });
          }
          const remEl = card.querySelector('.bom-po-remarks');
          cards.push({
            card_idx: cardIdx != null ? parseInt(cardIdx, 10) : 0,
            item_name: itemNameEl ? itemNameEl.value : '',
            warehouse: whEl ? whEl.value : '',
            planned_qty: qtyEl ? qtyEl.value : '',
            uom: uomEl ? uomEl.value : '',
            required_items: reqItems,
            production_order_remarks: remEl ? String(remEl.value || '').trim().substring(0, 254) : '',
          });
        });
        sections.push({
          process_name: processName,
          process_code: processCode,
          cards: cards,
        });
      });
      bomPayload.push({
        line_index: lineIndex,
        yield_loss_pct: 0,
        sections: sections,
      });
    });
    return bomPayload;
  }

  /** Wrap required-item rows in .bom-extra-wrap so serialization matches new-job form. */
  function ensureExtraWrap() {
    bomContainer.querySelectorAll('.bom-process-card').forEach(function (card) {
      const top = requiredItemsTop(card);
      if (!top) return;
      var wrap = top.querySelector('.bom-extra-wrap');
      if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'bom-extra-wrap';
        var addBtn = top.querySelector('.bom-add-extra');
        if (addBtn) top.insertBefore(wrap, addBtn);
        else top.appendChild(wrap);
      }
      Array.prototype.slice.call(top.querySelectorAll(':scope > .row.g-2')).forEach(function (r) {
        if (r.querySelector('.bom-itemcode-input') && r.parentElement === top) {
          wrap.appendChild(r);
        }
      });
    });
  }

  ensureExtraWrap();

  let fgOrWastageChanged = false;

  function syncAfterFgOrWastageChange() {
    fgOrWastageChanged = true;
    syncEditBomFromFg();
  }

  form.addEventListener('input', function (e) {
    const t = e.target;
    if (!t || !t.name) return;
    if (
      t.name.indexOf('fg_') === 0
      || t.name === 'detail_wastage_sheets'
    ) {
      syncAfterFgOrWastageChange();
    }
  });
  form.addEventListener('change', function (e) {
    const t = e.target;
    if (!t || !t.name) return;
    if (
      t.name.indexOf('fg_') === 0
      || t.name === 'detail_wastage_sheets'
    ) {
      syncAfterFgOrWastageChange();
    }
  });

  form.addEventListener('submit', function (e) {
    if (fgOrWastageChanged) {
      syncEditBomFromFg();
    }
    if (form.getAttribute('data-bom-studio-serialized') === '1') {
      form.removeAttribute('data-bom-studio-serialized');
      return;
    }
    const sub = e.submitter;
    if (!sub || sub.name !== 'bom_action' || sub.value !== 'save_bom_builder') return;
    e.preventDefault();
    bomHidden.value = JSON.stringify(serializeBomPayload());
    form.setAttribute('data-bom-studio-serialized', '1');
    form.requestSubmit(sub);
  });

  // Do not auto-wire on initial load: the edit screen must show the saved BOM exactly as-is.
})();
