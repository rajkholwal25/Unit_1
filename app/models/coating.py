from ..extensions import db


class CoatingType(db.Model):
    __tablename__ = 'coating_types'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
