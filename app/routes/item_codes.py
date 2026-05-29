from flask import Blueprint, render_template
from ..models import GeneratedFGItem, GeneratedProcessItem

item_codes_bp = Blueprint('item_codes', __name__)

@item_codes_bp.route('/')
def list_item_codes():
    # Query only item_code fields from FG and process items
    fg_codes = [r.item_code for r in GeneratedFGItem.query.with_entities(GeneratedFGItem.item_code).order_by(GeneratedFGItem.created_at.desc()).all()]
    proc_codes = [r.item_code for r in GeneratedProcessItem.query.with_entities(GeneratedProcessItem.item_code).order_by(GeneratedProcessItem.id.desc()).all()]
    # combine and deduplicate while preserving order (FG first)
    seen = set()
    combined = []
    for c in fg_codes + proc_codes:
        if c not in seen:
            seen.add(c)
            combined.append(c)
    return render_template('item_codes/list.html', item_codes=combined)
