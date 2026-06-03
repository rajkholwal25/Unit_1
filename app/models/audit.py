# import uuid removed
from datetime import datetime
from app.extensions import db


# ------------------------------------------------------------- JobStatusHistory
class JobStatusHistory(db.Model):
    """Immutable audit log of every status change on a job or header line.

    Rules:
    - Never delete or update rows in this table.
    - header_line_id is nullable — job-level changes leave it NULL,
      component-level changes populate it.
    - remark is required when rejecting (enforced in the service layer).
    """
    __tablename__ = 'job_status_history'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id = db.Column(
        db.String(20), db.ForeignKey('job_master.job_no', ondelete='CASCADE'),
        nullable=False, index=True
    )
    # NULL = job-level change
    from_status = db.Column(db.String(30), nullable=True)   # NULL on initial creation
    to_status = db.Column(db.String(30), nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    changed_by = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False
    )
    # DB column is legacy name ``reason`` (see merge migration); attribute stays ``remark``.
    remark = db.Column('reason', db.Text, nullable=True)

    # Relationships (backref 'job' defined in JobMaster)
    user = db.relationship('User', foreign_keys=[changed_by])

    def __repr__(self) -> str:
        return (
            f'<StatusHistory job={self.job_id}'
            f' {self.from_status}→{self.to_status}'
            f' at={self.changed_at}>'
        )


# ------------------------------------------------------------- IntegrationEvent
class IntegrationEvent(db.Model):
    """Log of every API call made to or from an external system (SAP, etc.).

    One row per API call attempt. Retries create new rows (retry_count tracks
    how many times we have attempted).

    request_payload / response_payload store the exact JSON so we can
    reproduce or debug any exchange without relying on external logs.

    state machine: pending → success
                           → failed  → retrying → success
                                                → failed (give up)
    """
    __tablename__ = 'integration_event'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id = db.Column(
        db.String(20), db.ForeignKey('job_master.job_no', ondelete='CASCADE'),
        nullable=False, index=True
    )

    target_system = db.Column(db.String(20), nullable=False, default='SAP_B1')
    # Descriptive action name e.g. 'create_production_order', 'sync_customer'
    action = db.Column(db.String(50), nullable=False)

    state = db.Column(
        db.Enum('pending', 'success', 'failed', 'retrying'),
        nullable=False, default='pending', index=True
    )

    # Full JSON payloads for traceability
    request_payload = db.Column(db.JSON, nullable=True)
    response_payload = db.Column(db.JSON, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    retry_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow,
        onupdate=datetime.utcnow, nullable=False
    )

    # Relationships (backref 'job' defined in JobMaster)

    # -------------------------------------------------------------- helpers
    def mark_success(self, response: dict) -> None:
        self.state = 'success'
        self.response_payload = response
        self.error_message = None

    def mark_failed(self, error: str, response: dict = None) -> None:
        self.state = 'failed'
        self.error_message = error
        if response:
            self.response_payload = response

    def mark_retrying(self) -> None:
        self.state = 'retrying'
        self.retry_count += 1

    def __repr__(self) -> str:
        return (
            f'<IntegrationEvent {self.action}'
            f' job={self.job_id} state={self.state}>'
        )
