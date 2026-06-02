from datetime import datetime

from ..extensions import db


class ItemMaster(db.Model):
    """Local registry of all FG and component item codes (SAP Item Master mirror)."""

    __tablename__ = 'item_master'

    id = db.Column(db.Integer, primary_key=True)
    item_code = db.Column(db.String(128), unique=True, nullable=False, index=True)
    item_name = db.Column(db.String(128), nullable=False)
    item_type = db.Column(db.String(16), nullable=False)  # fg | component
    parent_fg_code = db.Column(db.String(128), nullable=True, index=True)
    process_code = db.Column(db.String(32), nullable=True)
    material_type = db.Column(db.String(32), nullable=True)
    thickness = db.Column(db.Numeric(10, 3), nullable=True)
    coating = db.Column(db.String(16), nullable=True)
    pattern_id = db.Column(db.Integer, db.ForeignKey('patterns.id'), nullable=True)
    bom_template_id = db.Column(db.Integer, db.ForeignKey('bom_templates.id'), nullable=True)
    generated_fg_id = db.Column(db.Integer, db.ForeignKey('generated_fg_items.id'), nullable=True)
    warehouse_code = db.Column(db.String(64), nullable=True)
    items_group_code = db.Column(db.Integer, nullable=True)
    invntry_uom = db.Column(db.String(16), nullable=True)
    sal_unit_msr = db.Column(db.String(16), nullable=True)
    buy_unit_msr = db.Column(db.String(16), nullable=True)
    sales_item = db.Column(db.Boolean, default=False)
    sap_pushed = db.Column(db.Boolean, default=False)
    sap_pushed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
