# import uuid removed
from __future__ import annotations

import json
from datetime import datetime
from app.extensions import db
from app.utils.process_sequence import merge_ordered_unique_codes, ordered_unique_codes


# -------------------------------------------------------------------- Bom
class Bom(db.Model):
    """Versioned BOM attached to one JobHeaderLine.

    Rules:
    - Only one BOM per header_line can have is_active=True.
    - To revise a BOM, increment version and deactivate the old one.
    - template_name is set when a BOM is saved as a reusable template.
    """
    __tablename__ = 'bom'
    __table_args__ = (
        db.UniqueConstraint('detail_line_id', 'version', name='uq_bom_version'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    detail_line_id = db.Column(
        db.Integer,
        db.ForeignKey('job_detail_line.id', ondelete='CASCADE'),
        nullable=False, index=True
    )
    job_id = db.Column(
        db.String(20),
        db.ForeignKey('job_master.job_no', ondelete='SET NULL'),
        nullable=True, index=True
    )

    version = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    # Audit
    created_by = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # JSON array of process_code strings in planner order (unique codes, first-seen), including
    # outsourcing steps that have no BomStep row. Used for print slips; null = derive from steps only.
    slip_process_sequence_json = db.Column(db.Text, nullable=True)

    # Relationships
    steps = db.relationship(
        'BomStep', backref='bom', lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='BomStep.seq_no'
    )
    creator = db.relationship('User', foreign_keys=[created_by])

    # -------------------------------------------------------------- helpers
    @property
    def step_count(self) -> int:
        return self.steps.count()

    @property
    def slip_process_sequence_codes(self) -> list[str]:
        """Process codes saved for print slips, merged with the actual BOM step order.

        The stored JSON is the planner-facing sequence, which can include outsourcing
        steps that are not present as BomStep rows. If it is stale or incomplete, we
        backfill any missing real step codes from the BOM itself so print views stay
        aligned with the actual BOM.
        """
        raw = (self.slip_process_sequence_json or '').strip()
        stored_codes: list[str] = []
        if raw:
            try:
                arr = json.loads(raw)
                if isinstance(arr, list):
                    stored_codes = ordered_unique_codes(arr)
            except (TypeError, ValueError):
                stored_codes = []

        from app.services.unit1_processes import normalize_unit1_process_code

        step_codes = ordered_unique_codes(
            normalize_unit1_process_code(step.process_code)
            for step in self.steps.order_by(None).order_by(db.text('seq_no ASC')).all()
        )
        stored_codes = ordered_unique_codes(
            normalize_unit1_process_code(c) for c in stored_codes
        )

        if not stored_codes:
            return step_codes
        if not step_codes:
            return stored_codes

        return merge_ordered_unique_codes(stored_codes, step_codes)

    def deactivate(self) -> None:
        """Mark this BOM as inactive (used when creating a new revision)."""
        self.is_active = False

    def __repr__(self) -> str:
        return (
            f'<Bom detail={self.detail_line_id} v{self.version}'
            f' active={self.is_active}>'
        )


# ------------------------------------------------------------------ BomStep
class BomStep(db.Model):
    """One process step inside a BOM (e.g. Offset Printing, Lamination).

    seq_no controls the order steps are displayed and executed.
    process_code is a FK to ProcessMaster for controlled vocabulary.
    workcenter_code maps to an SAP workcenter resource.
    """
    __tablename__ = 'bom_step'
    __table_args__ = (
        db.UniqueConstraint('bom_id', 'seq_no', name='uq_bom_step_seq'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    bom_id = db.Column(
        db.Integer, db.ForeignKey('bom.id', ondelete='CASCADE'),
        nullable=False, index=True
    )
    seq_no = db.Column(db.Integer, nullable=False)

    # Process reference — FK to process_master.process_code
    process_code = db.Column(
        db.String(20),
        db.ForeignKey('process_master.process_code', ondelete='RESTRICT'),
        nullable=False
    )
    step_name = db.Column(db.String(100), nullable=False)
    warehouse = db.Column(db.String(20), nullable=True)
    uom          = db.Column(db.String(10), nullable=True) # Output UoM (Sheets, PCS, etc)
    planned_qty  = db.Column(db.Numeric(14, 4), nullable=True) # Quantity for this production step
    output_item_code = db.Column(db.String(50), nullable=True) # The specific FG Code for this card

    # SAP Production Order linkage — populated when "Create Production Orders" is triggered
    sap_doc_entry = db.Column(db.Integer, nullable=True)   # AbsoluteEntry from SAP response
    sap_doc_num   = db.Column(db.Integer, nullable=True)   # DocumentNumber from SAP response
    sap_warehouse  = db.Column(db.String(20), nullable=True)  # Warehouse used for the PO
    # User text for SAP Production Order header ``Remarks`` (OWOR); not on legacy JobCard table.
    production_order_remarks = db.Column(db.String(254), nullable=True)

    # Relationships
    inputs = db.relationship(
        'BomStepInput', backref='step', lazy='dynamic',
        cascade='all, delete-orphan'
    )
    process = db.relationship('ProcessMaster', foreign_keys=[process_code])

    def __repr__(self) -> str:
        return f'<BomStep bom={self.bom_id} seq={self.seq_no} [{self.process_code}]>'


# --------------------------------------------------------------- BomStepInput
class BomStepInput(db.Model):
    """A single material or consumable consumed by a BomStep.

    input_type distinguishes:
      raw_material  — paper, board, substrate (sourced from stock or PO)
      consumable    — ink, plate, glue, tape (internal stock)
      labour        — machine time / operator hours (SAP resource)

    is_consumed = True  → SAP 'backflush' issue method
    is_consumed = False → manual issue at shop floor
    """
    __tablename__ = 'bom_step_input'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    bom_step_id = db.Column(
        db.Integer, db.ForeignKey('bom_step.id', ondelete='CASCADE'),
        nullable=False, index=True
    )

    input_type = db.Column(
        db.Enum('raw_material', 'consumable', 'labour'),
        nullable=False, default='raw_material'
    )
    sap_item_code = db.Column(db.String(50), nullable=True, index=True)
    description = db.Column(db.String(200), nullable=True)
    uom = db.Column(db.String(10), nullable=True)

    # qty_per_job is the base quantity for the planned production quantity
    qty_per_job = db.Column(db.Numeric(14, 4), nullable=True)

    sap_warehouse = db.Column(db.String(20), nullable=True)

    # -------------------------------------------------------------- helpers
    @property
    def effective_qty(self) -> float:
        """Effective qty (scrap removed)."""
        return float(self.qty_per_job or 0.0)

    def __repr__(self) -> str:
        return (
            f'<BomStepInput step={self.bom_step_id}'
            f' item={self.sap_item_code} qty={self.qty_per_job}>'
        )
