from datetime import datetime

from ..extensions import db


class SapPushLog(db.Model):
    __tablename__ = 'sap_push_logs'

    id = db.Column(db.Integer, primary_key=True)
    request_payload = db.Column(db.JSON)
    response_payload = db.Column(db.JSON)
    status = db.Column(db.String(32))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
