from datetime import datetime
from .extensions import db
from werkzeug.security import generate_password_hash, check_password_hash

class Pattern(db.Model):
    __tablename__ = 'patterns'
    id = db.Column(db.Integer, primary_key=True)
    pattern_code = db.Column(db.String(10), unique=True, nullable=False)
    pattern_name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Pattern {self.pattern_code}:{self.pattern_name}>"


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(256), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(32), nullable=False, default='viewer')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_manager(self):
        return self.role == 'manager'

    def is_planner(self):
        return self.role == 'planner'

    def is_viewer(self):
        return self.role == 'viewer'

class MaterialType(db.Model):
    __tablename__ = 'material_types'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

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

class SapPushLog(db.Model):
    __tablename__ = 'sap_push_logs'
    id = db.Column(db.Integer, primary_key=True)
    request_payload = db.Column(db.JSON)
    response_payload = db.Column(db.JSON)
    status = db.Column(db.String(32))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
