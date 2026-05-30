"""Delete saved generated FG items and related process/BOM records."""

from sqlalchemy import or_

from ..extensions import db
from ..models import BomStructure, GeneratedFGItem, GeneratedProcessItem


def collect_item_codes(fg):
    codes = {fg.item_code}
    for proc in GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).all():
        codes.add(proc.item_code)
    return codes


def delete_bom_structures_for_codes(codes):
    if not codes:
        return
    BomStructure.query.filter(
        or_(
            BomStructure.parent_item_code.in_(codes),
            BomStructure.child_item_code.in_(codes),
        )
    ).delete(synchronize_session=False)


def delete_generated_fg_item(fg):
    """
    Remove one FG item and its process rows + BOM structure links.
    Returns (success, error_message).
    """
    if not fg:
        return False, 'Item not found'

    codes = collect_item_codes(fg)
    delete_bom_structures_for_codes(codes)
    GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).delete(synchronize_session=False)
    db.session.delete(fg)
    db.session.commit()
    return True, None
