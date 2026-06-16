from flask import Blueprint, jsonify
from flask_login import login_required
from app.models import JobMaster
from app.models.reference import ProcessMaster
from app.services.job_so_guard import so_number_usage_map

jobs_api_bp = Blueprint('jobs_api', __name__, url_prefix='/api/jobs')


@jobs_api_bp.route('/used-sales-orders')
@login_required
def used_sales_orders():
    """SO numbers already linked to a non-cancelled job (one SO → one job)."""
    usage = so_number_usage_map()
    return jsonify([
        {'so_no': so_no, 'job_no': job_no}
        for so_no, job_no in sorted(usage.items(), key=lambda x: x[0])
    ])


def _repeat_process_sequence_from_bom(active_bom):
    """Process names + outsourcing flags for repeat/reject load (matches slip order, includes outsource)."""
    if not active_bom:
        return [], []
    codes = list(active_bom.slip_process_sequence_codes)
    names: list[str] = []
    outsourcing: list[bool] = []
    for c in codes:
        c = (c or '').strip()
        if not c:
            continue
        pm = ProcessMaster.query.filter_by(process_code=c).first()
        nm = ((pm.name or '') if pm else '').strip() or c
        names.append(nm)
        cat = ((pm.category or '') if pm else '').strip().lower()
        outsourcing.append(cat == 'outsourcing')
    return names, outsourcing


@jobs_api_bp.route('/by-no/<job_no>')
@login_required
def get_job_by_no(job_no):
    # 1. Try JobMaster (New Manufacturing Jobs)
    job = JobMaster.query.filter_by(job_no=job_no).first()
    if job:
        header_lines = []
        header_idx_by_id = {}
        for hl in job.header_lines:
            # Try to find corresponding UPS from header first, then detail lines
            ups = hl.ups if hl.ups else 1
            # Check detail line as secondary source/verification
            first_dl = hl.job.detail_lines.filter_by(detail_no=hl.line_no).first()

            if first_dl and first_dl.ups and not hl.ups:
                ups = first_dl.ups

            header_lines.append({
                'line_no': hl.line_no,
                'sap_fg_item_code': hl.sap_fg_item_code,
                'sap_fg_item_name_snap': hl.sap_fg_item_name_snap,
                'fg_display_label': hl.fg_display_label,
                'dispatch_qty': float(hl.dispatch_qty) if hl.dispatch_qty else 0,
                'length': float(hl.length) if hl.length else None,
                'width': float(hl.width) if hl.width else None,
                'height': float(hl.height) if hl.height else None,
                'uom': hl.uom,
                'job_type': hl.job_type,
                'ups': ups,
                'yield_loss_pct': float(first_dl.yield_loss_pct) if first_dl and first_dl.yield_loss_pct is not None else None,
            })
            header_idx_by_id[hl.id] = len(header_lines) - 1

        detail_lines = []
        for dl in job.detail_lines:
            active_bom = dl.active_bom
            bom_data = None
            if active_bom:
                steps = []
                for step in active_bom.steps:
                    inputs = []
                    for inp in step.inputs:
                        inputs.append({
                            'input_type': inp.input_type,
                            'sap_item_code': inp.sap_item_code,
                            'description': inp.description,
                            'uom': inp.uom,
                            'qty_per_job': float(inp.qty_per_job) if inp.qty_per_job else 0,
                            'sap_warehouse': inp.sap_warehouse,
                        })
                    steps.append({
                        'seq_no': step.seq_no,
                        'process_code': step.process_code,
                        'step_name': step.step_name,
                        'warehouse': step.warehouse,
                        'sap_warehouse': step.sap_warehouse,
                        'uom': step.uom,
                        'planned_qty': float(step.planned_qty) if step.planned_qty else 0,
                        'output_item_code': step.output_item_code,
                        'production_order_remarks': (step.production_order_remarks or '')[:254],
                        'inputs': inputs,
                    })
                bom_data = {'steps': steps}

            seq_names, seq_out = _repeat_process_sequence_from_bom(active_bom)

            detail_lines.append({
                'detail_no': dl.detail_no,
                'element_name': dl.element_name,
                'ups': dl.ups,
                'yield_loss_pct': float(dl.yield_loss_pct) if dl.yield_loss_pct is not None else None,
                'raw_material_item_code': dl.raw_material_item_code,
                'paper_brand': dl.paper_brand,
                'mill': dl.mill,
                'total_sheets': dl.total_sheets,
                'paper_supplied_by': dl.paper_supplied_by if dl.paper_supplied_by else 'company',
                'wastage_pct': float(dl.wastage_pct) if dl.wastage_pct else 0,
                'wastage_sheets': dl.wastage_sheets,
                'sheet_length': float(dl.sheet_length) if dl.sheet_length else None,
                'sheet_width': float(dl.sheet_width) if dl.sheet_width else None,
                'gsm': dl.gsm,
                'thickness_mic': float(dl.thickness_mic) if getattr(dl, 'thickness_mic', None) else None,
                'chemical_coating_gsm': float(dl.chemical_coating_gsm) if getattr(dl, 'chemical_coating_gsm', None) else None,
                'metallisation_gsm': float(dl.metallisation_gsm) if getattr(dl, 'metallisation_gsm', None) else None,
                'chemical_item_code': getattr(dl, 'chemical_item_code', None),
                'chemical_qty_kg': float(dl.chemical_qty_kg) if getattr(dl, 'chemical_qty_kg', None) else None,
                'metallisation_qty_kg': float(dl.metallisation_qty_kg) if getattr(dl, 'metallisation_qty_kg', None) else None,
                'print_style': dl.print_style,
                'print_type': dl.print_type,
                'front_colours': dl.front_colours,
                'back_colours': dl.back_colours,
                'die_no': dl.die_no,
                'pasting_style': dl.pasting_style,
                'special_instructions': dl.special_instructions,
                'bom': bom_data,
                'process_sequence_names': seq_names,
                'process_sequence_outsourcing': seq_out,
                'fg_involved': [
                    header_idx_by_id.get(inv.header_line_id)
                    for inv in dl.fg_involved.all()
                    if header_idx_by_id.get(inv.header_line_id) is not None
                ],
            })

        return jsonify({
            'job_no': job.job_no,
            'sap_customer_code': job.sap_customer_code,
            'sap_customer_name_snap': job.sap_customer_name_snap,
            'sap_so_entry': job.sap_so_entry,
            'sap_so_number_snap': job.sap_so_number_snap,
            'sap_job_card_doc_entry': job.sap_job_card_doc_entry,
            'sap_job_card_doc_num_snap': job.sap_job_card_doc_num_snap,
            'sap_job_card_series_snap': job.sap_job_card_series_snap,
            'sap_job_card_title_snap': job.sap_job_card_title_snap,
            'priority': job.priority,
            'job_type_cat': job.job_type_cat,
            'job_series': job.job_series,
            'header_lines': header_lines,
            'detail_lines': detail_lines,
        })

    return jsonify({'error': 'Job not found'}), 404
