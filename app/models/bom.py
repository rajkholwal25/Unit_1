from datetime import datetime

from ..extensions import db


class BomTemplate(db.Model):
    __tablename__ = 'bom_templates'

    id = db.Column(db.Integer, primary_key=True)
    template_name = db.Column(db.String(128), nullable=False)
    process_sequence = db.Column(db.JSON, nullable=False)


class GeneratedFGItem(db.Model):
    __tablename__ = 'generated_fg_items'

    id = db.Column(db.Integer, primary_key=True)
    item_code = db.Column(db.String(128), unique=True, nullable=False)
    material_type = db.Column(db.String(32), nullable=False)
    thickness = db.Column(db.String(32), nullable=False)
    pattern_id = db.Column(db.Integer, db.ForeignKey('patterns.id'))
    bom_template_id = db.Column(db.Integer, db.ForeignKey('bom_templates.id'))
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
    parent_item_code = db.Column(db.String(128), nullable=False)
    child_item_code = db.Column(db.String(128), nullable=False)
    process_sequence = db.Column(db.JSON, nullable=True)
    warehouse_code = db.Column(db.String(64))
