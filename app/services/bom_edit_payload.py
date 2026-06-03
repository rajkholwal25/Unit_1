"""Convert saved BOM ↔ new-job ``bom_payload_json`` block and persist payload to DB.

Used by manufacturing job BOM edit (``edit_bom_spec``) so the same structure as
``job_cards/form.html`` can be edited and saved.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, Callable, Optional

from flask import current_app

from app.extensions import db
from app.models.mfg_bom import Bom, BomStep, BomStepInput
from app.models.job import JobDetailLine, JobHeaderLine, JobMaster
from app.models.reference import ProcessMaster
from app.services.mfg_bom_service import add_input, add_step
from app.services.sap_job_client import SAPClient, SAPClientError
from app.utils.process_sequence import merge_ordered_unique_codes, ordered_unique_codes

# Required-line warehouse after an outsourcing step (matches new-job ``form.html``).
SAP_OUTSOURCE_LINK_WAREHOUSE = 'OHJW-U2'


def unit1_default_uom() -> str:
    """Unit 1 film plant: quantities in kilograms."""
    return (
        current_app.config.get('UNIT1_DEFAULT_UOM')
        or current_app.config.get('SAP_BOM_PROCESS_ITEM_UOM')
        or 'KGS'
    ).strip().upper() or 'KGS'


def sections_for_slip_process_sequence(sections: Any) -> list[dict[str, Any]]:
    """Return section dicts safe for slip process extraction.

    Note: FG is no longer auto-appended. If the planner wants an FG step, it must be present
    in the planner sequence and/or BOM sections explicitly.
    """
    if not isinstance(sections, list):
        return []
    return [s for s in sections if isinstance(s, dict)]


def _process_codes_from_sections(
    sections: Any,
    *,
    resolve_process_code: Callable[..., Optional[str]],
) -> list[str]:
    """Return ordered unique process codes from BOM sections only."""
    secs = sections_for_slip_process_sequence(sections)
    parts: list[str] = []
    seen: set[str] = set()
    for sec in secs:
        process_name = str(sec.get('process_name') or '').strip()
        hinted = str(sec.get('process_code') or '').strip() or None
        pc = resolve_process_code(process_name, hinted)
        if not pc:
            continue
        key = pc.strip().upper()
        if key in seen:
            continue
        seen.add(key)
        parts.append(pc.strip())
    return ordered_unique_codes(parts)


def slip_process_sequence_json_from_sections(
    sections: Any,
    *,
    resolve_process_code: Callable[..., Optional[str]],
) -> Optional[str]:
    """Ordered unique process codes from BOM ``sections`` only (outsourcing often absent there).

    Matches legacy slip behaviour: first-seen order, no duplicate codes.
    """
    parts = _process_codes_from_sections(sections, resolve_process_code=resolve_process_code)
    if not parts:
        return None
    return json.dumps(parts, ensure_ascii=True)


def planner_line_sequences_from_form(raw: Optional[str]) -> dict[int, list[str]]:
    """Parse ``process_sequence_json`` from new-job / job-card form: ``line_index`` -> ordered process names."""
    out: dict[int, list[str]] = {}
    s = (raw or '').strip()
    if not s:
        return out
    try:
        data = json.loads(s)
    except (TypeError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    for item in data.get('lines') or []:
        if not isinstance(item, dict):
            continue
        try:
            li = int(item.get('line_index')) if item.get('line_index') is not None else 0
        except (TypeError, ValueError):
            li = 0
        seq = item.get('sequence') or []
        if not isinstance(seq, list):
            continue
        names = [str(x).strip() for x in seq if str(x or '').strip()]
        if names:
            out[li] = names
    return out


def slip_process_sequence_json_from_planner_and_sections(
    planner_names: Optional[list[str]],
    sections: Any,
    *,
    resolve_process_code: Callable[..., Optional[str]],
) -> Optional[str]:
    """Slip process order: planner sequence is primary, BOM sections backfill missing steps.

    Outsourcing never appears in serialized BOM ``sections`` (no cards), but it is still listed
    in ``process_sequence_json`` as a process **name** (e.g. ``Corrugation``). If the visible
    planner list misses a real BOM step, the step order from ``sections`` inserts it back.
    """
    parts: list[str] = []

    if planner_names:
        for nm in planner_names:
            name = str(nm or '').strip()
            if not name:
                continue
            pc = resolve_process_code(name, None) or resolve_process_code(name, name)
            if pc:
                parts.append(pc.strip())

    merged = merge_ordered_unique_codes(
        parts,
        _process_codes_from_sections(sections, resolve_process_code=resolve_process_code),
    )
    if not merged:
        return None
    return json.dumps(merged, ensure_ascii=True)


def is_die_split_process(process_code: str, process_name: str, pm_row: Optional[ProcessMaster]) -> bool:
    """True at the die-cutting split (aligned with ``form.html`` / ``recalculateBomQuantities``)."""
    c = (process_code or '').strip().upper()
    n = (process_name or '').strip().lower()
    if c in ('CV-DIE', 'DIE', 'DIECUT', 'DIECUTTING', 'DIE-CUT', 'DIE-TRY', 'DIE-TRAY', 'EMB+P'):
        return True
    if 'diecut' in n or ('die' in n and 'cut' in n):
        return True
    if pm_row and (pm_row.default_workcenter or '').strip().upper() == 'DIECUTTING':
        return True
    return False


def linkage_warehouse_from_previous_step(prev_st: Any, linked_item_code_u: str) -> Optional[str]:
    """Warehouse for the prefilled *previous-step output* line — mirrors ``form.html`` ``recalculateBomQuantities``.

    The UI sets ``whInput.value = prevOutputWh`` where ``prevOutputWh`` comes from the **previous**
    section's ``.bom-warehouse`` (step output WH), not the current step's warehouse.

    For combi diecutting, when the linked item is another FG's **negative** co-product row, the form
    uses that row's ``.bom-item-wh``; we match the corresponding ``BomStepInput`` (negative qty).
    """
    if prev_st is None:
        return None
    linked = (linked_item_code_u or '').strip().upper()
    if isinstance(prev_st, BomStep) and linked:
        try:
            for inp in prev_st.inputs.order_by(BomStepInput.id).all():
                c = (inp.sap_item_code or '').strip().upper()
                if c != linked:
                    continue
                try:
                    qf = float(inp.qty_per_job) if inp.qty_per_job is not None else None
                except (TypeError, ValueError):
                    qf = None
                if qf is not None and qf < 0:
                    w = (inp.sap_warehouse or '').strip()
                    if w:
                        return w[:20]
        except Exception:
            pass
    w = (getattr(prev_st, 'sap_warehouse', None) or getattr(prev_st, 'warehouse', None) or '')
    ws = str(w).strip()
    return ws[:20] if ws else None


def detail_material_row_index(job: JobMaster, detail: JobDetailLine) -> int:
    rows = job.detail_lines.order_by(JobDetailLine.detail_no).all()
    for i, d in enumerate(rows):
        if d.id == detail.id:
            return i
    return 0


def _payload_item_code(raw: Any) -> str:
    """SAP item code from studio fields that may contain ``CODE — Name``."""
    s = str(raw or '').strip()
    if '\u2014' in s:
        s = s.split('\u2014', 1)[0].strip()
    return s.upper()


def _payload_float(raw: Any) -> Optional[float]:
    if raw in (None, ''):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _payload_card_idx(card: dict[str, Any]) -> int:
    try:
        return int(card.get('card_idx')) if card.get('card_idx') is not None else 0
    except (TypeError, ValueError):
        return 0


def _qty_label(value: float) -> str:
    return f'{float(value):g}'


def validate_required_items_against_header_quantities(block: dict[str, Any]) -> None:
    """Validate process-output consumption without comparing unrelated step UoMs.

    A required item in a later process may consume a previously produced header item, but
    its positive quantity cannot be greater than that header row's planned quantity. This
    intentionally matches by SAP item code only; it does not force every step's header
    planned quantity to equal the previous step because process UoMs can legitimately differ.
    """
    sections = block.get('sections') if isinstance(block, dict) else None
    if not isinstance(sections, list):
        return

    produced_by_card: dict[int, dict[str, tuple[float, str]]] = {}
    produced_global: dict[str, tuple[float, str]] = {}

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        process_name = str(sec.get('process_name') or sec.get('process_code') or 'process').strip()
        cards = sec.get('cards') or []
        if not isinstance(cards, list):
            continue

        for card in cards:
            if not isinstance(card, dict):
                continue
            card_idx = _payload_card_idx(card)
            required_items = card.get('required_items') or []
            if not isinstance(required_items, list):
                continue
            for req in required_items:
                if not isinstance(req, dict):
                    continue
                code_u = _payload_item_code(req.get('sap_item_code'))
                if not code_u:
                    continue
                req_qty = _payload_float(req.get('qty_per_job'))
                # Negative rows are co-products/credits, not required consumption.
                if req_qty is None or req_qty <= 0:
                    continue
                produced = produced_by_card.get(card_idx, {}).get(code_u) or produced_global.get(code_u)
                if not produced:
                    continue
                header_qty, header_label = produced
                if req_qty > header_qty + 0.0001:
                    raise ValueError(
                        f'Required item {code_u} in {process_name} is {_qty_label(req_qty)}, '
                        f'but its header quantity is {_qty_label(header_qty)}'
                        f'{f" ({header_label})" if header_label else ""}. '
                        'A required item cannot exceed the quantity produced in its header.'
                    )

        # Make current section headers available to following process sections only.
        for card in cards:
            if not isinstance(card, dict):
                continue
            header_code_u = _payload_item_code(card.get('item_name'))
            header_qty = _payload_float(card.get('planned_qty'))
            if not header_code_u or header_qty is None:
                continue
            header_uom = str(card.get('uom') or '').strip()
            label = header_uom or ''
            card_idx = _payload_card_idx(card)
            produced_by_card.setdefault(card_idx, {})[header_code_u] = (header_qty, label)
            old_global = produced_global.get(header_code_u)
            if old_global is None or header_qty > old_global[0]:
                produced_global[header_code_u] = (header_qty, label)


def bom_block_from_saved_bom(
    job: JobMaster,
    detail: JobDetailLine,
    bom: Bom,
    *,
    header_line_for_bom_step: Callable[..., Any],
) -> dict[str, Any]:
    """Build one ``bom_payload_json`` block (with ``is_linkage`` hints for the studio UI)."""
    headers = list(job.header_lines.order_by(JobHeaderLine.line_no).all())
    mat_idx = detail_material_row_index(job, detail)
    steps = list(bom.steps.order_by(BomStep.seq_no).all())
    prev_out_by_card: dict[int, str] = {}
    # Per card: SAP item codes that were co-products (negative qty inputs) on the previous step —
    # next step's required line can link to these; must match ``is_linkage`` for edit-BOM sync.
    prev_coproduct_codes_by_card: dict[int, set[str]] = {}
    sections: list[dict[str, Any]] = []
    # Group by process_code only (new-job style): a single process section can have
    # multiple cards (FGs) and multiple production orders.
    sec_by_code: dict[str, dict[str, Any]] = {}
    has_reached_split = False

    for st in steps:
        hl = header_line_for_bom_step(job, st.output_item_code)
        cidx = 0
        if hl is not None and hl in headers:
            cidx = headers.index(hl)
        prev_full = (prev_out_by_card.get(cidx) or '').strip().upper()
        coprod_from_prev = prev_coproduct_codes_by_card.get(cidx, set())
        proc_code = (st.process_code or '').strip()
        if not proc_code:
            continue
        pm = ProcessMaster.query.filter_by(process_code=proc_code).first()
        proc_name = ((pm.name if pm else None) or (st.step_name or st.process_code or '')).strip()
        is_fg_step = proc_code.upper() in ('FG', 'PK-PACK')
        is_split_step = is_die_split_process(proc_code, proc_name, pm)
        is_pcs_step = bool(has_reached_split or is_split_step or is_fg_step)
        if is_split_step:
            has_reached_split = True

        req_rows: list[dict[str, Any]] = []
        inp_list = [
            inp
            for inp in st.inputs.order_by(BomStepInput.id).all()
            if (inp.sap_item_code or '').strip()
        ]
        for inp_idx, inp in enumerate(inp_list):
            code = (inp.sap_item_code or '').strip()
            code_u = code.upper()
            is_main_prev_link = bool(prev_full) and code_u == prev_full
            is_coprod_prev_link = bool(coprod_from_prev) and code_u in coprod_from_prev
            is_link = is_main_prev_link or is_coprod_prev_link
            # New-job ``createBomForLine`` marks extraRows[0] with data-is-prev-output so
            # ``recalculateBomQuantities`` can set qty on the first row even when it is not
            # a prev-step code match (e.g. first process RM / PSTR line).
            is_qty_driver = bool((not prev_full) and inp_idx == 0 and not is_coprod_prev_link)
            raw_inp_uom = (inp.uom or '').strip()
            if raw_inp_uom:
                row_uom = raw_inp_uom
            elif is_link and is_split_step:
                # ``recalculateBomQuantities``: incoming prev output on diecut uses Sheets.
                row_uom = unit1_default_uom()
            else:
                row_uom = unit1_default_uom()
            req_rows.append(
                {
                    'sap_item_code': code,
                    'description': (inp.description or '')[:200],
                    'warehouse': (inp.sap_warehouse or '') or '',
                    'qty_per_job': '' if inp.qty_per_job is None else str(float(inp.qty_per_job)),
                    'uom': row_uom,
                    'is_linkage': is_link,
                    'is_qty_driver': is_qty_driver,
                }
            )

        sec = sec_by_code.get(proc_code)
        if sec is None:
            sec = {
                'process_name': proc_name,
                'process_code': proc_code,
                'cards': [],
            }
            sec_by_code[proc_code] = sec
            sections.append(sec)

        raw_hdr_uom = (st.uom or '').strip()
        if raw_hdr_uom:
            hdr_uom = raw_hdr_uom
        else:
            hdr_uom = unit1_default_uom()

        sec['cards'].append(
            {
                'card_idx': cidx,
                'item_name': (st.output_item_code or '').strip(),
                'warehouse': (st.sap_warehouse or st.warehouse or '')[:20] or '',
                'planned_qty': '' if st.planned_qty is None else str(float(st.planned_qty)),
                'uom': hdr_uom,
                'required_items': req_rows,
                'production_order_remarks': (getattr(st, 'production_order_remarks', None) or '')[:254],
            }
        )
        if st.output_item_code:
            prev_out_by_card[cidx] = str(st.output_item_code).strip().upper()

        coprods_this_card: set[str] = set()
        for inp in st.inputs.order_by(BomStepInput.id).all():
            ic = (inp.sap_item_code or '').strip()
            if not ic:
                continue
            try:
                qf = float(inp.qty_per_job) if inp.qty_per_job is not None else None
            except (TypeError, ValueError):
                qf = None
            if qf is not None and qf < 0:
                coprods_this_card.add(ic.upper())
        if coprods_this_card:
            prev_coproduct_codes_by_card[cidx] = coprods_this_card
        else:
            prev_coproduct_codes_by_card.pop(cidx, None)

    try:
        yl = float(detail.yield_loss_pct) if detail.yield_loss_pct is not None else None
    except (TypeError, ValueError):
        yl = None
    return {
        'line_index': mat_idx,
        'yield_loss_pct': yl,
        'sections': sections,
    }


def gross_sheet_planned_for_detail(job: JobMaster, detail_line: JobDetailLine) -> int:
    """Unit 1 gross RM/input kg (legacy columns: total_sheets / wastage_sheets).

    Uses ``detail_line.total_sheets`` when set; else ``max(dispatch_qty)`` × (1 + yield loss %)
    + absolute wastage kg.
    """
    from app.utils.unit1_yield import detail_yield_loss_pct, rm_input_kg_from_fg
    header_lines = list(job.header_lines.order_by(JobHeaderLine.line_no).all())
    allowed_hdr_idxs: list[int] = []
    for inv in detail_line.fg_involved.all():
        hl = inv.header_line
        if hl and hl in header_lines:
            allowed_hdr_idxs.append(header_lines.index(hl))
    if not allowed_hdr_idxs and header_lines:
        allowed_hdr_idxs = list(range(len(header_lines)))

    try:
        kg_planned = float(detail_line.total_sheets or 0)
    except (TypeError, ValueError):
        kg_planned = 0.0
    if kg_planned > 0:
        return max(1, int(kg_planned + 0.999999))

    net_max_kg = 0.0
    for hi in allowed_hdr_idxs:
        hl2 = header_lines[hi]
        q = float(hl2.dispatch_qty or 0)
        if q > net_max_kg:
            net_max_kg = q
    try:
        wastage_kg = float(detail_line.wastage_sheets or 0)
    except (TypeError, ValueError):
        wastage_kg = 0.0
    n_conv = _num_converting_steps_for_detail(job, detail_line)
    y = detail_yield_loss_pct(detail_line, current_app.config)
    total = rm_input_kg_from_fg(net_max_kg, y, n_conv) + wastage_kg
    return max(1, int(total + 0.999999))


def _num_converting_steps_from_sections(sections: list) -> int:
    n = 0
    if not isinstance(sections, list):
        return 0
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        pn = (str(sec.get('process_name') or '')).strip().upper()
        pc = (str(sec.get('process_code') or '')).strip().upper()
        if pn in ('FG', 'PK-PACK', 'PK PACK') or pc in ('FG', 'PK-PACK', 'PKPACK'):
            continue
        n += 1
    return n


def _num_converting_steps_for_detail(job: JobMaster, detail_line: JobDetailLine) -> int:
    bom = detail_line.active_bom if detail_line else None
    if bom:
        n = 0
        for st in bom.steps.order_by(BomStep.seq_no).all():
            pc = (st.process_code or '').strip().upper()
            sn = (st.step_name or '').strip().upper()
            if sn == 'FG' or pc in ('FG', 'PK-PACK', 'PKPACK'):
                continue
            n += 1
        if n > 0:
            return n
    return 3  # Unit 1 default EMB + SLT + MET when BOM not built yet


def fg_planned_qty_pcs(job: JobMaster, detail_line: JobDetailLine, card_hdr: JobHeaderLine) -> float:
    """Unit 1 FG planned qty in KGS — header dispatch quantity (SO line qty), not sheets × UPS."""
    if card_hdr is not None:
        try:
            dq = float(card_hdr.dispatch_qty or 0)
            if dq > 0:
                return dq
        except (TypeError, ValueError):
            pass
    return float(gross_sheet_planned_for_detail(job, detail_line))


def persist_bom_payload_block(
    job: JobMaster,
    detail_line: JobDetailLine,
    bom: Bom,
    block: dict[str, Any],
    sap_client: Optional[SAPClient],
    *,
    resolve_process_code: Callable[..., Optional[str]],
    process_item_code_fn: Callable[..., str],
    header_line_for_bom_step: Callable[..., Any],
    synthetic_display_name_for_process_item_code: Callable[..., str],
    planner_sequence: Optional[list[str]] = None,
) -> None:
    """Populate ``bom`` (empty steps) from one payload block (mirrors ``new_job`` BOM path)."""
    validate_required_items_against_header_quantities(block)

    header_lines = list(job.header_lines.order_by(JobHeaderLine.line_no).all())
    if not header_lines:
        raise ValueError('Job has no header lines')

    try:
        line_idx_i = int(block.get('line_index'))
    except (TypeError, ValueError):
        line_idx_i = 0
    hdr = header_lines[line_idx_i] if 0 <= line_idx_i < len(header_lines) else header_lines[0]

    allowed_hdr_idxs: list[int] = []
    for inv in detail_line.fg_involved.all():
        hl = inv.header_line
        if hl and hl in header_lines:
            allowed_hdr_idxs.append(header_lines.index(hl))
    # If fg_involved links are missing (older DB / migration not applied), fall back to all headers.
    if not allowed_hdr_idxs and header_lines:
        allowed_hdr_idxs = list(range(len(header_lines)))

    yl_raw = block.get('yield_loss_pct')
    if detail_line is not None and yl_raw not in (None, ''):
        try:
            from decimal import Decimal

            detail_line.yield_loss_pct = Decimal(str(max(0.0, min(float(yl_raw), 99.9))))
        except (TypeError, ValueError):
            pass

    sections = block.get('sections') or []
    n_conv = _num_converting_steps_from_sections(sections)
    from app.utils.unit1_yield import detail_yield_loss_pct, rm_input_kg_from_fg

    net_max_kg = 0.0
    for hi in allowed_hdr_idxs:
        hl2 = header_lines[hi]
        q = float(hl2.dispatch_qty or 0)
        if q > net_max_kg:
            net_max_kg = q
    try:
        wastage_kg = float(detail_line.wastage_sheets or 0)
    except (TypeError, ValueError):
        wastage_kg = 0.0
    try:
        kg_planned = float(detail_line.total_sheets or 0)
    except (TypeError, ValueError):
        kg_planned = 0.0
    if kg_planned > 0:
        sheet_planned = max(1, int(kg_planned + 0.999999))
    else:
        y = detail_yield_loss_pct(detail_line, current_app.config)
        sheet_planned = max(
            1,
            int(rm_input_kg_from_fg(net_max_kg, y, n_conv) + wastage_kg + 0.999999),
        )

    if not isinstance(sections, list) or not sections:
        raise ValueError('BOM payload has no sections')

    # Mirror new-job builder: FG is auto-added as the final step if missing.
    has_fg = False
    for s in sections:
        if not isinstance(s, dict):
            continue
        pn = str(s.get('process_name') or '').strip().upper()
        pc = str(s.get('process_code') or '').strip().upper()
        if pn == 'FG' or pc in ('FG', 'PK-PACK'):
            has_fg = True
            break
    if not has_fg:
        sections = list(sections) + [{'process_name': 'FG', 'process_code': 'FG', 'cards': []}]

    sap_item_group = int(current_app.config.get('SAP_BOM_PROCESS_ITEM_GROUP_CODE', 115))
    sap_item_uom = unit1_default_uom()

    seq = 10
    prev_outputs_by_card: dict[int, str] = {}
    last_step_by_card: dict[int, Any] = {}
    pending_outsource_wh_by_card: dict[int, str] = {}
    has_reached_split = False
    created_output_codes_u: set[str] = set()

    from app.services.mfg_warehouse import (
        default_sap_warehouse,
        process_wh_by_tail,
        warehouse_for_process_code,
    )

    PROCESS_WH_BY_TAIL = process_wh_by_tail()
    PROCESS_WAREHOUSES_BY_TITLE = {
        'Embossing': warehouse_for_process_code('EMB'),
        'Slitting': warehouse_for_process_code('SLT'),
        'Metallizing': warehouse_for_process_code('MET'),
        'Heat seal': warehouse_for_process_code('HRI'),
        'Coating': warehouse_for_process_code('COAT'),
        'FG': warehouse_for_process_code('FG'),
    }

    def _tail(code: str) -> str:
        c = (code or '').strip().upper()
        return c.split('-')[-1] if c else ''

    def _extract_fg_num(code: str) -> str:
        if not code:
            return 'FG'
        m = re.search(r'(FG\d+)', code, re.IGNORECASE)
        return m.group(1).upper() if m else code.strip().upper()

    def _process_out_code_like(prev_out_code_u: str, fg_code_for_card: str) -> str:
        """Rewrite prev output to this card's FG base + same process tail (Unit 1, no GEN)."""
        from app.services.unit1_processes import unit1_fg_base_code, unit1_process_item_code

        c = (prev_out_code_u or '').strip().upper()
        base = unit1_fg_base_code(fg_code_for_card)
        if c.startswith(base + '-'):
            tail = c[len(base) + 1 :]
            return unit1_process_item_code(fg_code_for_card, tail)
        if '-' in c:
            tail = c.split('-')[-1]
            return unit1_process_item_code(fg_code_for_card, tail)
        return unit1_process_item_code(fg_code_for_card, c)

    def _process_wh_for_title(title: str) -> str:
        t = (title or '').strip()
        return PROCESS_WAREHOUSES_BY_TITLE.get(t) or default_sap_warehouse()

    def _resolve_required_item_wh(
        req_wh_raw: str,
        *,
        has_prev_real_step: bool,
        step_sap_wh: Optional[str],
        default_wh_eff: str,
        process_name_for_wh: str,
        item_code_u: str,
        rm_code_u: str,
        preserve_ii_rm: bool = False,
    ) -> str:
        """Default warehouse for a BOM required line — mirrors new-job ``form.html`` row models.

        * Unset or ``II-RM`` (studio placeholder) → process/step default.
        * First real step + item matches detail raw material → ``II-PSTR`` (same as first-step ``reqWh`` in form).
        """
        w = (req_wh_raw or '').strip()
        if preserve_ii_rm and w.upper() == 'II-RM':
            return 'II-RM'
        if w.upper() in ('', 'II-RM'):
            if (not has_prev_real_step) and rm_code_u and item_code_u == rm_code_u:
                return 'II-PSTR'
            base = (
                (step_sap_wh or '').strip()
                or (default_wh_eff or '').strip()
                or _process_wh_for_title(process_name_for_wh)
                or default_sap_warehouse()
            )
            return (base[:20] if base else default_sap_warehouse())
        return w[:20]

    def _looks_like_job_process_output(code_u: str) -> bool:
        """True if code matches our intermediate process output pattern (FGxxx-ELM-PROC...)."""
        c = (code_u or '').strip().upper()
        if not c.startswith('FG') or '-' not in c:
            return False
        # Expected: FG#######-EEE-TAIL...
        parts = c.split('-', 2)
        if len(parts) < 3:
            return False
        fg_part, el_part, tail = parts[0], parts[1], parts[2]
        if not fg_part or not el_part or not tail:
            return False
        if len(el_part) < 2:
            return False
        # Tail must at least look like a process code (letters/digits/+/-)
        return bool(re.match(r'^[A-Z0-9][A-Z0-9+\-]{1,}$', tail))

    def _payload_req_item_code(req: dict[str, Any]) -> str:
        raw_item = str(req.get('sap_item_code') or '').strip()
        if '\u2014' in raw_item:
            raw_item = raw_item.split('\u2014', 1)[0].strip()
        return raw_item.upper()

    def _payload_req_for_codes(required_items: Any, *codes: str) -> Optional[dict[str, Any]]:
        wanted = {str(c or '').strip().upper() for c in codes if str(c or '').strip()}
        if not wanted or not isinstance(required_items, list):
            return None
        for req in required_items:
            if not isinstance(req, dict):
                continue
            if _payload_req_item_code(req) in wanted:
                return req
        return None

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        process_name = str(sec.get('process_name') or '').strip()
        process_code = resolve_process_code(
            process_name,
            str(sec.get('process_code') or '').strip() or None,
        )
        if not process_code:
            continue
        pm = ProcessMaster.query.filter_by(process_code=process_code).first()
        is_fg_step = str(process_code or '').strip().upper() in ('FG', 'PK-PACK')
        is_split_step = is_die_split_process(process_code, process_name, pm)
        is_pcs_step = bool(has_reached_split or is_split_step or is_fg_step)
        if pm and (pm.category or '').strip().lower() == 'outsourcing':
            out_wh = (pm.default_workcenter or '').strip() or 'II-CORU'
            if allowed_hdr_idxs:
                for hi in allowed_hdr_idxs:
                    prev_step = last_step_by_card.get(hi)
                    if prev_step is not None:
                        prev_step.sap_warehouse = out_wh
                    pending_outsource_wh_by_card[hi] = out_wh
            else:
                for hi in list(last_step_by_card.keys()):
                    last_step_by_card[hi].sap_warehouse = out_wh
                    pending_outsource_wh_by_card[hi] = out_wh
            continue

        cards = sec.get('cards') or []
        if not isinstance(cards, list):
            continue

        # Snapshot previous-step outputs BEFORE creating any steps in this section.
        # This mirrors the new-job JS `prevOutputs` variable, which does not change
        # while iterating cards for the same process.
        prev_step_by_card_snapshot: dict[int, Any] = dict(last_step_by_card)

        # New-job style card expansion:
        # - before split: single card (first FG)
        # - at split (diecut): single card
        # - after split: one card per FG
        # - FG step: one card per FG
        base_card_idx = allowed_hdr_idxs[0] if allowed_hdr_idxs else 0
        expected_card_idxs = (
            allowed_hdr_idxs
            if (is_fg_step or (has_reached_split and (not is_split_step)))
            else [base_card_idx]
        )
        # Flip split AFTER determining current step expected cards (new-job logic)
        if is_split_step:
            has_reached_split = True

        cards_by_idx: dict[int, dict] = {}
        for c in cards:
            if not isinstance(c, dict):
                continue
            raw_ci = c.get('card_idx', 0)
            try:
                ci = int(raw_ci) if raw_ci is not None else 0
            except (TypeError, ValueError):
                ci = 0
            cards_by_idx[ci] = c
        fallback_card = cards_by_idx.get(base_card_idx) or (cards[0] if cards else None)

        for c_pos, payload_card_idx in enumerate(expected_card_idxs):
            card = cards_by_idx.get(payload_card_idx) or fallback_card
            # If there is no card data for this FG, still create the step using defaults (new-job behavior).
            if not isinstance(card, dict):
                card = {}

            card_hdr = hdr
            if 0 <= payload_card_idx < len(header_lines):
                card_hdr = header_lines[payload_card_idx]
            ups = 1.0
            try:
                # For detail lines after the first, per-detail-line UPS drives PCS conversions
                # (e.g. Diecutting converting Sheets → PCS). Prefer detail_line.ups when present.
                if detail_line is not None and detail_line.ups is not None and int(float(detail_line.ups)) > 0:
                    ups = float(int(float(detail_line.ups)))
                else:
                    ups = float(card_hdr.ups or 1) if card_hdr else 1.0
            except Exception:
                ups = 1.0
            if ups <= 0:
                ups = 1.0

            step = add_step(
                bom=bom,
                process_code=process_code,
                step_name=process_name or process_code,
                seq_no=seq + c_pos,
            )
            card_wh = (str(card.get('warehouse') or '').strip() or None)
            default_wh = PROCESS_WH_BY_TAIL.get(_tail(process_code)) or PROCESS_WH_BY_TAIL.get(_tail(process_name))
            if not default_wh:
                default_wh = _process_wh_for_title(process_name)
            # If UI didn't set it (or it carried a generic II-RM), prefer new-job default per process.
            if (not card_wh) or (card_wh.strip().upper() == 'II-RM'):
                card_wh = default_wh
            step.sap_warehouse = card_wh
            step.uom = (str(card.get('uom') or '').strip() or None)
            # New-job ``buildBomSection``: card header UoM is PCS after split / at die / FG, else Sheets.
            if not (step.uom or '').strip():
                step.uom = unit1_default_uom()
            po_rm = str(
                card.get('production_order_remarks') or card.get('sap_po_remarks') or ''
            ).strip()
            step.production_order_remarks = po_rm[:254] if po_rm else None
            try:
                step_planned = card.get('planned_qty')
                step.planned_qty = float(step_planned) if step_planned not in (None, '') else None
            except (TypeError, ValueError):
                step.planned_qty = None
            if step.planned_qty is None:
                if is_fg_step:
                    try:
                        step.planned_qty = (
                            float(fg_planned_qty_pcs(job, detail_line, card_hdr)) if card_hdr else 1.0
                        )
                    except Exception:
                        step.planned_qty = 1.0
                else:
                    try:
                        card_q = card.get('planned_qty')
                        if card_q not in (None, ''):
                            step.planned_qty = float(card_q)
                        else:
                            step.planned_qty = float(sheet_planned or 1)
                    except (TypeError, ValueError):
                        step.planned_qty = float(sheet_planned or 1)

            if is_fg_step:
                output_item_code = (card_hdr.sap_fg_item_code or '').strip()
            else:
                output_item_code = process_item_code_fn(
                    (card_hdr.sap_fg_item_code or ''),
                    (detail_line.element_name if detail_line else ''),
                    process_code,
                )
            step.output_item_code = output_item_code[:50] if output_item_code else None
            if step.output_item_code:
                created_output_codes_u.add(step.output_item_code.strip().upper())

            fg_full_name = (card_hdr.sap_fg_item_name_snap or card_hdr.sap_fg_item_code or 'FG').strip()
            proc_full_name = (process_name or process_code or 'PROC').strip()
            output_item_name = (
                fg_full_name[:100] if is_fg_step else f'{fg_full_name}-{proc_full_name}'[:100]
            )

            if sap_client and (not is_fg_step) and output_item_code:
                try:
                    sap_client.ensure_item_exists(
                        output_item_code[:50],
                        output_item_name,
                        base_fg_code=(card_hdr.sap_fg_item_code or '') or None,
                        item_group_code=sap_item_group,
                        sales_uom=step.uom or sap_item_uom,
                    )
                except SAPClientError as e:
                    current_app.logger.warning('[BOM-STUDIO] ensure_item_exists %s: %s', output_item_code, e)

            # ---------------- linkage (previous step output) ----------------
            # New-job logic:
            # - If previous step had a single output card, ALL current cards link to it.
            prev_st = prev_step_by_card_snapshot.get(payload_card_idx)
            if prev_st is None and len(prev_step_by_card_snapshot) == 1:
                prev_st = next(iter(prev_step_by_card_snapshot.values()))

            # Special case (new-job combi logic): split step is single-card but produces
            # per-FG outputs (co-products). Post-split cards should link to their own FG output.
            prev_out_code_u = (prev_st.output_item_code or '').strip().upper() if prev_st else ''
            if (
                prev_st
                and prev_out_code_u
                and len(prev_step_by_card_snapshot) == 1
                and (not is_split_step)
                and len(expected_card_idxs) > 1
            ):
                fg_for_card = (card_hdr.sap_fg_item_code or '').strip()
                if fg_for_card:
                    prev_out_code_u = _process_out_code_like(prev_out_code_u, fg_for_card)

            req_items = card.get('required_items') or []
            if not isinstance(req_items, list):
                req_items = []

            if prev_st and prev_out_code_u:
                linkage_req = _payload_req_for_codes(
                    req_items,
                    prev_out_code_u,
                    (prev_st.output_item_code or '').strip(),
                )
                # Link qty/uom should follow previous step output. For synthesized co-product
                # outputs (other FGs) use the current card's planned qty (PCS) like new-job.
                try:
                    link_qty = float(prev_st.planned_qty) if prev_st.planned_qty is not None else float(step.planned_qty or 1)
                except Exception:
                    link_qty = float(step.planned_qty or 1)
                if prev_out_code_u != (prev_st.output_item_code or '').strip().upper():
                    link_qty = float(step.planned_qty or 1)
                if linkage_req:
                    try:
                        payload_link_qty = linkage_req.get('qty_per_job')
                        if payload_link_qty not in (None, ''):
                            link_qty = float(payload_link_qty)
                    except (TypeError, ValueError):
                        pass
                # New-job ``recalculateBomQuantities``: diecut step forces Sheets on prev-output row.
                link_uom = ((prev_st.uom or '') or unit1_default_uom()).strip() or unit1_default_uom()
                link_uom = link_uom[:10]
                if is_split_step:
                    link_uom = unit1_default_uom()
                if linkage_req:
                    payload_link_uom = str(linkage_req.get('uom') or '').strip()
                    if payload_link_uom:
                        link_uom = payload_link_uom[:10]
                force_ohjw = payload_card_idx in pending_outsource_wh_by_card
                # New-job: prefilled linkage WH = previous step output WH (or co-product row WH), not current step.
                link_wh_src = linkage_warehouse_from_previous_step(prev_st, prev_out_code_u)
                payload_link_wh = None
                if linkage_req:
                    raw_payload_wh = str(linkage_req.get('warehouse') or '').strip()
                    if raw_payload_wh:
                        rm_u = ((detail_line.raw_material_item_code or '').strip().upper() if detail_line else '')
                        payload_link_wh = _resolve_required_item_wh(
                            raw_payload_wh,
                            has_prev_real_step=bool(prev_st),
                            step_sap_wh=str(step.sap_warehouse or '').strip() or None,
                            default_wh_eff=(default_wh or _process_wh_for_title(process_name) or default_sap_warehouse()).strip(),
                            process_name_for_wh=process_name,
                            item_code_u=prev_out_code_u,
                            rm_code_u=rm_u,
                            preserve_ii_rm=bool(linkage_req.get('warehouse_user_edited')),
                        )
                link_wh = SAP_OUTSOURCE_LINK_WAREHOUSE if force_ohjw else (
                    payload_link_wh
                    or link_wh_src
                    or (str(step.sap_warehouse or '').strip()[:20] if step.sap_warehouse else None)
                    or (default_wh or None)
                )
                link_desc = (
                    synthetic_display_name_for_process_item_code(job, detail_line, prev_out_code_u[:50]) or ''
                ).strip()
                if not link_desc:
                    link_desc = (
                        f'Output of {prev_st.step_name or prev_st.process_code or "prior step"}'
                    )[:200]
                add_input(
                    step=step,
                    input_type='raw_material',
                    sap_item_code=prev_out_code_u[:50],
                    description=link_desc[:200],
                    uom=link_uom,
                    qty_per_job=link_qty,
                    sap_warehouse=link_wh,
                )
                # Do NOT pop ``pending_outsource_wh_by_card`` here: the required_items loop may still
                # add rows that consume the previous output (combi FG rewrite vs studio code). New-job
                # path (``jobs.new_job``) clears pending only after all inputs for the card. Popping here
                # forced ``force_ohjw`` false and fell through to ``link_wh_src`` / req WH = II-CORU.

            for req in req_items:
                if not isinstance(req, dict):
                    continue
                raw_item = str(req.get('sap_item_code') or '').strip()
                # Preserve the SAP-stored case of the ItemCode — SAP's Service Layer key lookup
                # and ProductionOrderLines.ItemNo validation are case-sensitive.
                if '\u2014' in raw_item:
                    _code, _name = raw_item.split('\u2014', 1)
                    sap_item_code = _code.strip()
                    name_from_ui = _name.strip()
                else:
                    sap_item_code = raw_item
                    name_from_ui = ''
                saved_description = str(req.get('description') or '').strip()
                if not sap_item_code:
                    sap_item_code = (prev_outputs_by_card.get(payload_card_idx, '') or '')
                resolved_item_name = (
                    (name_from_ui[:200] if name_from_ui else '')
                    or (saved_description[:200] if saved_description else '')
                ).strip()
                if not resolved_item_name:
                    resolved_item_name = synthetic_display_name_for_process_item_code(
                        job, detail_line, sap_item_code
                    )
                if not resolved_item_name:
                    resolved_item_name = sap_item_code

                hl_for_input = header_line_for_bom_step(job, sap_item_code)
                base_fg_for_item = (
                    (hl_for_input.sap_fg_item_code or '').strip()
                    if hl_for_input
                    else ''
                ) or (card_hdr.sap_fg_item_code or '').strip()

                raw_req_uom = str(req.get('uom') or '').strip()
                if raw_req_uom:
                    req_uom_resolved = raw_req_uom[:10]
                else:
                    # New-job first required row model: Sheets at die split, else PCS if PCS step else Sheets.
                    req_uom_resolved = unit1_default_uom()[:10]

                if sap_client and sap_item_code:
                    try:
                        sap_client.ensure_item_exists(
                            sap_item_code[:50],
                            resolved_item_name[:200],
                            base_fg_code=base_fg_for_item or None,
                            item_group_code=100,
                            sales_uom=req_uom_resolved,
                        )
                    except Exception as e:
                        current_app.logger.warning(
                            'Could not verify component %s in SAP: %s', sap_item_code, e
                        )

                qty_val = req.get('qty_per_job')
                try:
                    qty_per_job = float(qty_val) if qty_val not in (None, '') else None
                except (TypeError, ValueError):
                    qty_per_job = None
                if not sap_item_code or qty_per_job is None:
                    continue
                # Skip if this row repeats the injected linkage line (studio may use raw or combi-rewritten code).
                if prev_st and prev_out_code_u:
                    raw_prev_out = (prev_st.output_item_code or '').strip().upper()
                    if sap_item_code.strip().upper() in (prev_out_code_u, raw_prev_out):
                        continue
                # Drop intermediate process-output item codes unless it is the linkage item.
                # Keeps each step tied to the active previous-step output only. When linkage is
                # injected (``prev_out_code_u``), drop any other synthetic process-output row from
                # the studio JSON even if ``preserve_on_regen`` — otherwise removing e.g. Embossing
                # leaves a preserved Embossing code on Diecutting while the description/UI label
                # may still read like Printing.
                code_u = sap_item_code.strip().upper() if sap_item_code else ''
                preserve = bool(req.get('preserve_on_regen'))
                stale_intermediate_output = (
                    bool(code_u)
                    and _looks_like_job_process_output(code_u)
                    and (not prev_out_code_u or code_u != prev_out_code_u)
                )
                if stale_intermediate_output and (
                    is_fg_step or not preserve or bool(prev_out_code_u)
                ):
                    continue
                # Also drop any process outputs created in this pass (safety net).
                if (
                    (not preserve)
                    and sap_item_code
                    and sap_item_code.strip().upper() in created_output_codes_u
                    and (not prev_out_code_u or sap_item_code.strip().upper() != prev_out_code_u)
                ):
                    continue
                prev_out_code = (prev_outputs_by_card.get(payload_card_idx) or '').strip().upper()
                is_prev_out_line = bool(prev_out_code) and sap_item_code.strip().upper() == prev_out_code
                force_ohjw = (payload_card_idx in pending_outsource_wh_by_card and is_prev_out_line)
                desc = resolved_item_name[:200]
                rm_u = ((detail_line.raw_material_item_code or '').strip().upper() if detail_line else '')
                default_wh_eff = (default_wh or _process_wh_for_title(process_name) or default_sap_warehouse()).strip()
                req_wh_resolved = _resolve_required_item_wh(
                    str(req.get('warehouse') or ''),
                    has_prev_real_step=bool(prev_st),
                    step_sap_wh=str(step.sap_warehouse or '').strip() or None,
                    default_wh_eff=default_wh_eff,
                    process_name_for_wh=process_name,
                    item_code_u=sap_item_code.strip().upper(),
                    rm_code_u=rm_u,
                )
                add_input(
                    step=step,
                    input_type='raw_material',
                    sap_item_code=sap_item_code[:50],
                    description=desc,
                    uom=req_uom_resolved,
                    qty_per_job=qty_per_job,
                    sap_warehouse=SAP_OUTSOURCE_LINK_WAREHOUSE if force_ohjw else req_wh_resolved,
                )

            # First real step: auto-add paper/raw material like new-job, unless already present
            # (regeneration may preserve the old raw material row via ``preserve_on_regen``).
            if not prev_st:
                rm = (detail_line.raw_material_item_code or '').strip()
                if rm:
                    rm_u = rm.upper()
                    already = False
                    for r0 in card.get('required_items') or []:
                        if not isinstance(r0, dict):
                            continue
                        c0 = str(r0.get('sap_item_code') or '').strip().upper()
                        if c0.split('\u2014', 1)[0].strip() == rm_u:
                            already = True
                            break
                    if not already:
                        desc = ((detail_line.paper_brand or '') or 'Paper')[:200]
                        add_input(
                            step=step,
                            input_type='raw_material',
                            sap_item_code=rm[:50],
                            description=desc,
                            uom=unit1_default_uom(),
                            qty_per_job=float(sheet_planned or 1),
                            sap_warehouse='FBD-RM',
                        )

            # --- New-job combi: diecut single-card adds negative co-products for other FGs ---
            if (
                is_split_step
                and (not is_fg_step)
                and len(allowed_hdr_idxs) > 1
                and payload_card_idx == base_card_idx
            ):
                die_title = (process_name or (pm.name if pm else '') or process_code or '').strip()
                co_wh = _process_wh_for_title(die_title)
                for other_hi in allowed_hdr_idxs[1:]:
                    other_hl = header_lines[other_hi] if 0 <= other_hi < len(header_lines) else None
                    if not other_hl:
                        continue
                    try:
                        other_ups = float(other_hl.ups or 1) or 1.0
                    except Exception:
                        other_ups = 1.0
                    try:
                        other_dq = float(other_hl.dispatch_qty or 0)
                    except (TypeError, ValueError):
                        other_dq = 0.0
                    other_planned = other_dq if other_dq > 0 else float(sheet_planned or 1)
                    other_fg = (other_hl.sap_fg_item_code or '').strip()
                    if not other_fg:
                        continue
                    other_item_code = process_item_code_fn(
                        other_fg,
                        (detail_line.element_name if detail_line else ''),
                        process_code,
                    )
                    other_name = synthetic_display_name_for_process_item_code(job, detail_line, other_item_code)
                    if not other_name:
                        other_fg_full = (other_hl.sap_fg_item_name_snap or other_hl.sap_fg_item_code or 'FG').strip()
                        other_name = f'{other_fg_full[:100]}-{proc_full_name}'[:100]
                    if sap_client and other_item_code:
                        try:
                            sap_client.ensure_item_exists(
                                other_item_code[:50],
                                other_name[:200],
                                base_fg_code=other_fg or None,
                                item_group_code=sap_item_group,
                                sales_uom=unit1_default_uom(),
                            )
                        except SAPClientError as e:
                            current_app.logger.warning('[BOM-STUDIO] ensure_item_exists %s: %s', other_item_code, e)
                    add_input(
                        step=step,
                        input_type='raw_material',
                        sap_item_code=other_item_code[:50],
                        description=(other_name or other_item_code)[:200],
                        uom=unit1_default_uom(),
                        qty_per_job=-1.0 * float(other_planned),
                        sap_warehouse=co_wh,
                    )
                    created_output_codes_u.add(other_item_code.strip().upper())

                # Propagation after split: each FG consumes its own die output (even though only one die step exists).
                for hi in allowed_hdr_idxs:
                    hl_i = header_lines[hi] if 0 <= hi < len(header_lines) else None
                    if not hl_i:
                        continue
                    fg_i = (hl_i.sap_fg_item_code or '').strip()
                    if not fg_i:
                        continue
                    out_i = process_item_code_fn(
                        fg_i,
                        (detail_line.element_name if detail_line else ''),
                        process_code,
                    ).strip()
                    try:
                        ups_i = float(hl_i.ups or 1) or 1.0
                    except Exception:
                        ups_i = 1.0
                    try:
                        dq_i = float(hl_i.dispatch_qty or 0)
                    except (TypeError, ValueError):
                        dq_i = 0.0
                    planned_i = dq_i if dq_i > 0 else float(sheet_planned or 1)
                    last_step_by_card[hi] = SimpleNamespace(
                        output_item_code=out_i[:50],
                        planned_qty=planned_i,
                        uom=unit1_default_uom(),
                        sap_warehouse=step.sap_warehouse,
                        warehouse=step.warehouse,
                        step_name=step.step_name,
                        process_code=process_code,
                    )
                    prev_outputs_by_card[hi] = out_i.strip().upper()
                if payload_card_idx in pending_outsource_wh_by_card:
                    pending_outsource_wh_by_card.pop(payload_card_idx, None)
                # Prevent the generic per-card update below from overwriting the per-FG split mapping.
                continue

            prev_outputs_by_card[payload_card_idx] = (output_item_code or '').strip().upper()
            last_step_by_card[payload_card_idx] = step
            if payload_card_idx in pending_outsource_wh_by_card:
                pending_outsource_wh_by_card.pop(payload_card_idx, None)

        seq += 10

    bom.slip_process_sequence_json = slip_process_sequence_json_from_planner_and_sections(
        planner_sequence,
        sections,
        resolve_process_code=resolve_process_code,
    )

    db.session.flush()
