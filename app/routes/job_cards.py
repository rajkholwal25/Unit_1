import json
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models.job import JobMaster
from app.models.job_card import JobCard, VALID_TRANSITIONS
from app.models.job_card_items import JobCardMaterial, JobCardStatusHistory
from app.routes.job_cards_helpers import parse_float, parse_int, sap_po_doc_entries_list
from app.services.sap_job_client import SAPClientError, get_sap_client
from app.services.prinect_service import push_jdf_for_job_card

job_cards_bp = Blueprint('job_cards', __name__, url_prefix='/job-cards')


@job_cards_bp.route('/')
@login_required
def list():
    return redirect(
        url_for(
            'jobs.list_jobs',
            jc_status=request.args.get('status', ''),
            jc_priority=request.args.get('priority', ''),
            jc_search=request.args.get('search', '').strip(),
        )
    )


def _line_key_selected(r: Dict[str, Any]) -> str:
    return '|'.join([
        str(r.get('doc_entry') or ''),
        str(r.get('line_num') or ''),
        str(r.get('fg_code') or ''),
    ])


def _fg_group_key_selected(r: Dict[str, Any]) -> Any:
    """Same FG number (FG + digits) → one merge bucket; else one row per distinct line key."""
    fc = str(r.get('fg_code') or '').strip()
    m = re.search(r'(FG\d+)', fc, re.IGNORECASE)
    if m:
        return ('fg', m.group(1).upper())
    return ('line', _line_key_selected(r))


def _club_selected_lines(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge rows that share the same FG number: sum quantity/ups; keep SO line from the row with higher quantity."""
    if not rows:
        return []

    def _qf(x: Dict[str, Any]) -> float:
        v = x.get('quantity')
        if v in (None, ''):
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    groups = OrderedDict()
    for r in rows:
        k = _fg_group_key_selected(r)
        if k not in groups:
            groups[k] = []
        groups[k].append(r)

    out: List[Dict[str, Any]] = []
    for grp in groups.values():
        if len(grp) == 1:
            out.append(grp[0])
            continue
        winner = grp[0]
        wq = _qf(winner)
        for r in grp[1:]:
            rq = _qf(r)
            if rq > wq:
                winner = r
                wq = rq
        merged = dict(winner)
        total_q = sum(_qf(x) for x in grp)
        merged['quantity'] = total_q if any(x.get('quantity') not in (None, '') for x in grp) else None
        ups_sum = 0.0
        ups_any = False
        for x in grp:
            u = x.get('ups')
            if u in (None, ''):
                continue
            try:
                ups_sum += float(u)
                ups_any = True
            except (TypeError, ValueError):
                pass
        if ups_any:
            merged['ups'] = ups_sum
        out.append(merged)
    return out


def _normalize_selected_lines_json(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return ''
    try:
        data = json.loads(s)
    except Exception:
        return ''
    if not isinstance(data, list):
        return ''

    def _to_float(v):
        if v in (None, ''):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        fg_code = str(r.get('fg_code') or '').strip()
        fg_name = str(r.get('fg_name') or '').strip()
        if not fg_code:
            continue
        out.append({
            'doc_entry': r.get('doc_entry'),
            'so_no': str(r.get('so_no') or '').strip(),
            'line_num': r.get('line_num'),
            'fg_code': fg_code,
            'fg_name': fg_name,
            'ups': _to_float(r.get('ups')),
            'quantity': _to_float(r.get('quantity')),
            'carton_length_mm': _to_float(r.get('carton_length_mm')),
            'carton_width_mm': _to_float(r.get('carton_width_mm')),
            'carton_height_mm': _to_float(r.get('carton_height_mm')),
        })
    out = _club_selected_lines(out)
    return json.dumps(out, ensure_ascii=True)


def _normalize_process_sequence_json(raw: str) -> str:
    """Accept legacy JSON array of process names or ``{"lines":[{line_index, element, sequence},...]}``."""
    s = (raw or '').strip()
    if not s:
        return ''
    try:
        data = json.loads(s)
    except Exception:
        return ''
    lines_out = []
    if isinstance(data, list):
        seq = [str(x).strip() for x in data if str(x or '').strip()]
        lines_out.append({'line_index': 0, 'element': '', 'sequence': seq})
    elif isinstance(data, dict):
        for item in data.get('lines') or []:
            if not isinstance(item, dict):
                continue
            li = item.get('line_index')
            try:
                li = int(li) if li is not None else 0
            except (TypeError, ValueError):
                li = 0
            el = str(item.get('element') or '').strip()
            seq = item.get('sequence')
            if not isinstance(seq, list):
                seq = []
            seq = [str(x).strip() for x in seq if str(x or '').strip()]
            lines_out.append({'line_index': li, 'element': el, 'sequence': seq})
    else:
        return ''
    return json.dumps({'lines': lines_out}, ensure_ascii=True)


def _process_sequence_for_form(raw: Optional[str]) -> Dict[str, Any]:
    """Parse DB value for the job card form (hidden field + JS). Legacy list → single line."""
    if not raw:
        return {'lines': []}
    try:
        data = json.loads(raw)
    except Exception:
        return {'lines': []}
    if isinstance(data, list):
        seq = [str(x).strip() for x in data if str(x or '').strip()]
        return {'lines': [{'line_index': 0, 'element': '', 'sequence': seq}]}
    if isinstance(data, dict) and 'lines' in data:
        return data
    return {'lines': []}


def _process_sequence_for_view(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Rows for view template: ``[{line_index, element, sequence}, ...]`` sorted by line_index."""
    data = _process_sequence_for_form(raw)
    rows = data.get('lines') or []
    if not isinstance(rows, list):
        return []
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        li = item.get('line_index')
        try:
            li = int(li) if li is not None else 0
        except (TypeError, ValueError):
            li = 0
        seq = item.get('sequence') or []
        if not isinstance(seq, list):
            seq = []
        seq = [str(x).strip() for x in seq if str(x or '').strip()]
        out.append({
            'line_index': li,
            'element': str(item.get('element') or '').strip(),
            'sequence': seq,
        })
    out.sort(key=lambda r: r['line_index'])
    return out


def _save_job_card_header(job_card, form):
    job_card.sap_customer_code = form.get('customer_id')
    selected_json = _normalize_selected_lines_json(form.get('sap_selected_lines_json', ''))
    job_card.sap_selected_lines_json = selected_json or None
    rows = json.loads(selected_json) if selected_json else []

    # Header core values are now derived from selected SO/FG lines.
    first = rows[0] if rows else {}
    total_qty = 0.0
    for r in rows:
        q = r.get('quantity')
        if q is None:
            continue
        try:
            total_qty += float(q)
        except (TypeError, ValueError):
            continue

    job_card.product_name = (first.get('fg_name') or first.get('fg_code') or '').strip()
    job_card.product_description = None
    job_card.item_code = (first.get('fg_code') or '').strip() or None
    job_card.quantity = total_qty
    job_card.uom = (job_card.uom or 'PCS').strip() if getattr(job_card, 'uom', None) else 'PCS'
    job_card.delivery_date = form.get('delivery_date') or job_card.delivery_date
    job_card.priority = form.get('priority') or job_card.priority or 'medium'

    job_card.sap_so_doc_num = (first.get('so_no') or '').strip() or None
    job_card.sap_so_doc_entry = first.get('doc_entry') if first else None
    job_card.sap_mjd1_line_code = str(first.get('line_num')) if first and first.get('line_num') is not None else None
    job_card.sap_fg_code = (first.get('fg_code') or '').strip() or None
    job_card.sap_fg_name_snap = (first.get('fg_name') or '').strip() or None
    job_card.carton_length_mm = first.get('carton_length_mm') if first else None
    job_card.carton_width_mm = first.get('carton_width_mm') if first else None
    job_card.carton_height_mm = first.get('carton_height_mm') if first else None
    process_json = _normalize_process_sequence_json(form.get('process_sequence_json', ''))
    job_card.process_sequence_json = process_json or None
    jk = form.get('job_kind', '').strip().lower()
    job_card.job_kind = jk if jk in ('new', 'repeat') else None


def _save_materials(job_card, form):
    idx = 0
    while True:
        name = form.get(f'mat_name_{idx}', '').strip()
        if not name:
            break

        material = JobCardMaterial(
            job_card_id=job_card.id,
            material_name=name,
            material_code=form.get(f'mat_code_{idx}', '').strip() or None,
            paper_type=form.get(f'mat_paper_type_{idx}', '').strip() or None,
            gsm=form.get(f'mat_gsm_{idx}', '').strip() or None,
            width_mm=parse_float(form, f'mat_width_{idx}', None),
            height_mm=parse_float(form, f'mat_height_{idx}', None),
            length_mm=parse_float(form, f'mat_length_{idx}', None),
            ink_colors=form.get(f'mat_ink_colors_{idx}', '').strip() or None,
            quantity_required=float(form.get(f'mat_qty_{idx}', 0) or 0),
            uom=form.get(f'mat_uom_{idx}', 'KGS').strip(),
            remarks=form.get(f'mat_remarks_{idx}', '').strip() or None,
            num_ups=parse_int(form, f'mat_num_ups_{idx}'),
            element_name=form.get(f'mat_element_name_{idx}', '').strip() or None,
            raw_material_item_code=form.get(f'mat_raw_material_{idx}', '').strip() or None,
            paper_brand=form.get(f'mat_paper_brand_{idx}', '').strip() or None,
            total_sheets=parse_float(form, f'mat_total_sheets_{idx}'),
            paper_supplied_by=(
                'company'
                if (form.get(f'mat_paper_supplied_{idx}', '').strip().lower() == 'press')
                else (form.get(f'mat_paper_supplied_{idx}', '').strip() or None)
            ),
            wastage_pct=parse_float(form, f'mat_wastage_pct_{idx}'),
            wastage_sheets=parse_float(form, f'mat_wastage_sheets_{idx}'),
            print_style=form.get(f'mat_print_style_{idx}', '').strip() or None,
            mill=form.get(f'mat_mill_{idx}', '').strip() or None,
            detail_special_instructions=form.get(f'mat_detail_instr_{idx}', '').strip() or None,
            die_no=form.get(f'mat_die_no_{idx}', '').strip() or None,
            front_colours=form.get(f'mat_front_colours_{idx}', '').strip() or None,
            back_colours=form.get(f'mat_back_colours_{idx}', '').strip() or None,
            pasting_style=form.get(f'mat_pasting_style_{idx}', '').strip() or None,
            print_type_metpet=form.get(f'mat_print_type_{idx}', '').strip() or None,
        )
        db.session.add(material)
        idx += 1


@job_cards_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    if not current_user.has_role('admin', 'planner'):
        flash('Legacy job cards are read-only. Contact planner/admin for Manufacturing Jobs access.', 'warning')
        return redirect(url_for('jobs.list_jobs'))

    flash('Legacy job card creation is disabled. Use the Manufacturing Job form.', 'info')
    return redirect(url_for('jobs.new_job'))


@job_cards_bp.route('/<int:job_card_id>')
@login_required
def view(job_card_id):
    job_card = JobCard.query.get(job_card_id)
    manufacturing_job = JobMaster.query.get(job_card_id)
    if manufacturing_job and not job_card:
        return redirect(url_for('jobs.view_job', job_id=manufacturing_job.id))

    if not job_card:
        job_card = JobCard.query.get_or_404(job_card_id)
    materials = job_card.materials.all()
    history = job_card.status_history.all()
    valid_transitions = VALID_TRANSITIONS.get(job_card.status, [])
    po_entries = sap_po_doc_entries_list(job_card)
    return render_template(
        'job_cards/view.html',
        job_card=job_card,
        materials=materials,
        process_sequence_lines=_process_sequence_for_view(job_card.process_sequence_json),
        history=history,
        valid_transitions=valid_transitions,
        sap_po_entries=po_entries,
    )


@job_cards_bp.route('/<int:job_card_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(job_card_id):
    job_card = JobCard.query.get_or_404(job_card_id)
    flash('Legacy job cards are now read-only.', 'warning')
    return redirect(url_for('job_cards.view', job_card_id=job_card.id))


def _sap_actions_for_status(job_card, new_status: str) -> None:
    if not current_app.config.get('SAP_SERVICE_LAYER_URL'):
        return
    entries = sap_po_doc_entries_list(job_card)
    if not entries:
        return
    client = get_sap_client()
    try:
        if new_status == 'released':
            for de in entries:
                client.release_production_order(de)
        elif new_status == 'closed':
            for de in entries:
                client.close_production_order(de)
    finally:
        client.logout()


@job_cards_bp.route('/<int:job_card_id>/status', methods=['POST'])
@login_required
def change_status(job_card_id):
    job_card = JobCard.query.get_or_404(job_card_id)
    flash('Legacy job cards are now read-only. Status changes are disabled.', 'warning')
    return redirect(url_for('job_cards.view', job_card_id=job_card.id))


@job_cards_bp.route('/<int:job_card_id>/delete', methods=['POST'])
@login_required
def delete(job_card_id):
    _ = JobCard.query.get_or_404(job_card_id)
    flash('Legacy job cards are now read-only. Deletion is disabled.', 'warning')
    return redirect(url_for('jobs.list_jobs'))
