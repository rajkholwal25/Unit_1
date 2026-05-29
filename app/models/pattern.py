from datetime import datetime

from ..extensions import db


class Pattern(db.Model):
    __tablename__ = 'patterns'

    id = db.Column(db.Integer, primary_key=True)
    pattern_code = db.Column(db.String(10), unique=True, nullable=False)
    pattern_name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Pattern {self.pattern_code}:{self.pattern_name}>'
