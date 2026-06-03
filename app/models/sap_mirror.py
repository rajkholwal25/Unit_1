"""Persistent SAP mirror tables (scheduled refresh; not an in-memory cache)."""
from datetime import datetime, timezone

from app.extensions import db


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp for model defaults."""
    return datetime.now(timezone.utc)


class SapCustomerMirror(db.Model):
    """Copy of SAP Business Partners (customers) from Service Layer."""

    __tablename__ = 'sap_customer_mirror'

    card_code = db.Column(db.String(30), primary_key=True)
    card_name = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    synced_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)

    def __repr__(self) -> str:
        return f'<SapCustomerMirror {self.card_code}>'



class SapItemMirror(db.Model):
    """Copy of SAP Item Master (OITM) for FG/raw-material selection in forms.

    Sync stores **active** items only (SAP ``Valid`` = ``tYES``).
    """

    __tablename__ = 'sap_item_mirror'

    item_code = db.Column(db.String(50), primary_key=True)
    item_name = db.Column(db.String(200), nullable=True)
    item_type = db.Column(
        db.Enum('fg', 'raw_material', 'consumable', 'service'),
        nullable=True, index=True,
    )
    uom = db.Column(db.String(10), nullable=True)
    default_warehouse = db.Column(db.String(20), nullable=True, index=True)
    synced_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)

    def __repr__(self) -> str:
        return f'<SapItemMirror {self.item_code}: {self.item_name}>'


class SapMirrorSyncState(db.Model):
    """Singleton row (id=1) tracking last mirror sync."""

    __tablename__ = 'sap_mirror_sync_state'

    id = db.Column(db.Integer, primary_key=True)
    last_full_sync_at = db.Column(db.DateTime, nullable=True)
    last_customer_sync_at = db.Column(db.DateTime, nullable=True)
    last_item_sync_at = db.Column(db.DateTime, nullable=True)
    customer_row_count = db.Column(db.Integer, nullable=True)
    item_row_count = db.Column(db.Integer, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
