"""Delete saved BOM variants; Item Master catalog is not removed."""

from ..extensions import db
from ..models import BomStructure, GeneratedFGItem, GeneratedProcessItem
from .item_master_service import delete_for_fg


def delete_bom_structures_for_fg(fg):
    if not fg:
        return
    BomStructure.query.filter_by(generated_fg_id=fg.id).delete(synchronize_session=False)


def delete_generated_fg_item(fg):
    if not fg:
        return False, 'Item not found'

    delete_for_fg(fg)
    delete_bom_structures_for_fg(fg)
    GeneratedProcessItem.query.filter_by(fg_item_id=fg.id).delete(synchronize_session=False)
    db.session.delete(fg)
    db.session.commit()
    return True, None
