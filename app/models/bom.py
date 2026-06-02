from datetime import datetime

from ..extensions import db


class BomTemplate(db.Model):
    __tablename__ = 'bom_templates'

    id = db.Column(db.Integer, primary_key=True)
    template_name = db.Column(db.String(128), nullable=False)
    process_sequence = db.Column(db.JSON, nullable=False)


class GeneratedFGItem(db.Model):
    __tablename__ = 'generated_fg_items'
    __table_args__ = (
        db.UniqueConstraint('item_code', 'bom_template_id', name='uq_fg_item_code_template'),
    )

    id = db.Column(db.Integer, primary_key=True)
    item_code = db.Column(db.String(128), nullable=False, index=True)
    material_type = db.Column(db.String(32), nullable=False)
    thickness = db.Column(db.Numeric(10, 3), nullable=False)
    coating = db.Column(db.String(16), nullable=True)
    pattern_id = db.Column(db.Integer, db.ForeignKey('patterns.id'))
    bom_template_id = db.Column(db.Integer, db.ForeignKey('bom_templates.id'))
    raw_material_item_code = db.Column(db.String(128), nullable=True)
    yield_loss_pct = db.Column(db.Numeric(5, 2), nullable=False, default=2)
    sap_bom_pushed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class GeneratedProcessItem(db.Model):
    __tablename__ = 'generated_process_items'

    id = db.Column(db.Integer, primary_key=True)
    fg_item_id = db.Column(db.Integer, db.ForeignKey('generated_fg_items.id'))
    process_code = db.Column(db.String(32), nullable=False)
    item_code = db.Column(db.String(128), nullable=False)
    warehouse_code = db.Column(db.String(64))


class BomStructure(db.Model):
    __tablename__ = 'bom_structures'

    id = db.Column(db.Integer, primary_key=True)
    generated_fg_id = db.Column(db.Integer, db.ForeignKey('generated_fg_items.id'), nullable=True)
    parent_item_code = db.Column(db.String(128), nullable=False)
    child_item_code = db.Column(db.String(128), nullable=False)
    process_sequence = db.Column(db.JSON, nullable=True)
    line_type = db.Column(db.String(16), nullable=False, default='process')
    quantity = db.Column(db.Numeric(12, 6), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    warehouse_code = db.Column(db.String(64))
