from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from io import BytesIO

from app.services.roll_grn_import import build_grn_batch_template_bytes, import_grn_batch_excel
from app.services.roll_grn_service import get_roll_grn_by_number, list_roll_grns
from app.utils.auth import role_required

roll_grn_bp = Blueprint('roll_grn', __name__, url_prefix='/grn')

_ALLOWED_EXTENSIONS = {'.xlsx'}


@roll_grn_bp.route('/upload', methods=['GET', 'POST'])
@roll_grn_bp.route('/create', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'planner', 'operator')
def upload():
    if request.method == 'POST':
        upload_file = request.files.get('excel_file')
        if not upload_file or not upload_file.filename:
            flash('Please choose an Excel file (.xlsx) to upload.', 'danger')
            return redirect(url_for('roll_grn.upload'))

        fname = upload_file.filename.lower()
        if not any(fname.endswith(ext) for ext in _ALLOWED_EXTENSIONS):
            flash('Only .xlsx Excel files are supported.', 'danger')
            return redirect(url_for('roll_grn.upload'))

        try:
            file_bytes = upload_file.read()
            if not file_bytes:
                flash('Uploaded file is empty.', 'danger')
                return redirect(url_for('roll_grn.upload'))

            result = import_grn_batch_excel(file_bytes, created_by_id=current_user.id)

            if result.added_count:
                flash(
                    f'{result.added_count} new GRN batch number(s) created.',
                    'success',
                )
            if result.skipped_count:
                flash(
                    f'{result.skipped_count} row(s) skipped — already in database (same supplier + roll number).',
                    'info',
                )
            for err in result.errors[:8]:
                flash(err, 'warning')
            if len(result.errors) > 8:
                flash(f'…and {len(result.errors) - 8} more row errors.', 'warning')

            if not result.added_count and not result.skipped_count and result.errors:
                flash('No rows were imported. Fix the Excel file and try again.', 'danger')
                return redirect(url_for('roll_grn.upload'))

            return redirect(url_for('roll_grn.index'))
        except ValueError as e:
            flash(str(e), 'danger')
        except Exception as e:
            flash(f'Could not import Excel: {str(e)[:200]}', 'danger')

    return render_template('roll_grn/upload.html')


@roll_grn_bp.route('/template')
@login_required
@role_required('admin', 'planner', 'operator')
def download_template():
    data = build_grn_batch_template_bytes()
    return send_file(
        BytesIO(data),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='grn_batch_upload_template.xlsx',
    )


@roll_grn_bp.route('/')
@login_required
def index():
    entries = list_roll_grns()
    return render_template('roll_grn/list.html', entries=entries)


@roll_grn_bp.route('/<grn_number>')
@login_required
def view(grn_number: str):
    entry = get_roll_grn_by_number(grn_number)
    if not entry:
        flash('GRN batch number not found.', 'warning')
        return redirect(url_for('roll_grn.index'))
    return render_template('roll_grn/view.html', entry=entry)
