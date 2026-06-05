"""Raw-material roll GRN entries (internal GRN number per supplier roll)."""

from datetime import datetime

from app.extensions import db


class RollGrnEntry(db.Model):
    __tablename__ = 'roll_grn_entry'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    grn_number = db.Column(db.String(16), unique=True, nullable=False, index=True)

    supplier_name = db.Column(db.String(200), nullable=False)
    supplier_roll_number = db.Column(db.String(100), nullable=False)

    film_type = db.Column(db.String(50), nullable=False)
    coating = db.Column(db.String(50), nullable=False)

    width_mm = db.Column(db.Numeric(12, 3), nullable=False)
    thickness_mic = db.Column(db.Numeric(12, 3), nullable=False)
    length_mtr = db.Column(db.Numeric(12, 3), nullable=False)

    gross_weight_kg = db.Column(db.Numeric(12, 3), nullable=False)
    net_weight_kg = db.Column(db.Numeric(12, 3), nullable=False)
    core_weight_kg = db.Column(db.Numeric(12, 3), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f'<RollGrnEntry {self.grn_number}>'
