# import uuid removed
import math
import re
from datetime import datetime
from app.extensions import db


# ------------------------------------------------------------------ constants
# Valid status values — used for validation in services layer
JOB_STATUSES = (
    'open',
    'staged',
    'released',
    'closed',
    'cancelled',
)

LINE_STATUSES = (
    'open',
    'staged',
    'released',
    'closed',
)

COMPONENT_TYPES = (
    'outer_carton',
    'inner_carton',
    'label',
    'insert',
    'leaflet',
    'sleeve',
    'tray',
    'other',
)


# ------------------------------------------------------------------ JobMaster
class JobMaster(db.Model):
    __tablename__ = 'job_master'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_no = db.Column(db.String(20), unique=True, nullable=False, index=True)

    # SAP customer linkage
    # sap_customer_code references SAP CardCode from mirror/live lookup
    # sap_customer_name_snap is stored at creation so historical records are stable
    sap_customer_code = db.Column(db.String(20), nullable=True, index=True)
    sap_customer_name_snap = db.Column(db.String(200), nullable=True)

    # SAP Sales Order linkage
    # sap_so_entry = SAP internal DocEntry (integer) used in API calls
    # sap_so_number_snap = human-readable SO number shown on job card
    sap_so_entry = db.Column(db.Integer, nullable=True)
    sap_so_number_snap = db.Column(db.String(20), nullable=True)

    # SAP source job-card linkage for traceability
    sap_job_card_doc_entry = db.Column(db.Integer, nullable=True, index=True)
    sap_job_card_doc_num_snap = db.Column(db.String(30), nullable=True)
    sap_job_card_series_snap = db.Column(db.String(30), nullable=True)
    sap_job_card_title_snap = db.Column(db.String(200), nullable=True)

    # Workflow
    overall_status = db.Column(
        db.String(30), nullable=False, default='open', index=True
    )
    priority = db.Column(
        db.Enum('low', 'normal', 'urgent'),
        nullable=False, default='normal'
    )
    job_type_cat = db.Column(db.String(20), nullable=True, default='Mono') # Mono, Rigid, Commercial
    job_series = db.Column(db.String(20), nullable=True, default='Normal') # Normal, Rejection
    # For Repeat/Rejection: referenced original job no (if any)
    original_job_no = db.Column(db.String(20), nullable=True, index=True)
    delivery_date = db.Column(db.Date, nullable=True)
    remarks = db.Column(db.Text, nullable=True)

    # Ownership
    assigned_planner_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    created_by = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow,
        onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    header_lines = db.relationship(
        'JobHeaderLine', backref='job', lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='JobHeaderLine.line_no'
    )
    status_history = db.relationship(
        'JobStatusHistory', backref='job', lazy='dynamic',
        foreign_keys='JobStatusHistory.job_id',
        order_by='JobStatusHistory.changed_at'
    )
    integration_events = db.relationship(
        'IntegrationEvent', backref='job', lazy='dynamic',
        foreign_keys='IntegrationEvent.job_id'
    )
    creator = db.relationship(
        'User', foreign_keys=[created_by], backref='created_jobs'
    )
    assigned_planner = db.relationship(
        'User', foreign_keys=[assigned_planner_id], backref='assigned_jobs'
    )
    detail_lines = db.relationship(
        'JobDetailLine', backref='job', lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='JobDetailLine.detail_no'
    )
    detail_fg_involved = db.relationship(
        'JobDetailLineFgInvolved', backref='job_master', lazy='dynamic',
        cascade='all, delete-orphan',
        foreign_keys='JobDetailLineFgInvolved.job_id',
    )

    # -------------------------------------------------------------- properties
    @property
    def is_editable(self) -> bool:
        """True while the job is **not** released yet: only ``open`` and ``staged``."""
        return self.overall_status in ('open', 'staged')

    @property
    def can_stage(self) -> bool:
        return self.overall_status == 'open'

    @property
    def can_release(self) -> bool:
        return self.overall_status == 'staged'

    @property
    def header_line_count(self) -> int:
        return self.header_lines.count()

    def __repr__(self) -> str:
        return f'<JobMaster {self.job_no} [{self.overall_status}]>'


# ------------------------------------------------------------ JobHeaderLine
class JobHeaderLine(db.Model):
    __tablename__ = 'job_header_line'
    __table_args__ = (
        db.UniqueConstraint('job_id', 'line_no', name='uq_job_line_no'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id = db.Column(
        db.String(20), db.ForeignKey('job_master.job_no', ondelete='CASCADE'),
        nullable=False, index=True
    )
    line_no = db.Column(db.Integer, nullable=False)


    # Component identity
    sap_fg_item_code = db.Column(db.String(50), nullable=True, index=True)
    sap_fg_item_name_snap = db.Column(db.String(200), nullable=True)

    # Quantity & dimensions
    dispatch_qty = db.Column(db.Numeric(12, 3), nullable=True)
    length = db.Column(db.Numeric(10, 2), nullable=True)
    width = db.Column(db.Numeric(10, 2), nullable=True)
    height = db.Column(db.Numeric(10, 2), nullable=True)
    uom = db.Column(db.String(10), nullable=True)
    ups = db.Column(db.Integer, nullable=True, default=1)

    # Job type: new / repeat / sample / revision
    job_type = db.Column(db.String(30), nullable=True)

    # Timestamps
    released_at = db.Column(db.DateTime, nullable=True)

    # -------------------------------------------------------------- properties
    @property
    def is_editable(self) -> bool:
        """Same rule as the parent job (open / staged)."""
        return bool(self.job and self.job.is_editable)

    @property
    def fg_display_label(self) -> str:
        """Human FG name (pattern name + MM); code stays in ``sap_fg_item_code``."""
        from app.services.unit1_item_naming import unit1_fg_human_label_from_item_code

        code = (self.sap_fg_item_code or '').strip()
        if code:
            lbl = unit1_fg_human_label_from_item_code(code)
            if lbl:
                return lbl
        return (self.sap_fg_item_name_snap or '').strip() or '—'

    def __repr__(self) -> str:
        return f'<HeaderLine job={self.job_id} L{self.line_no}>'


# ------------------------------------------------------------- JobDetailLine
class JobDetailLine(db.Model):
    __tablename__ = 'job_detail_line'
    __table_args__ = (
        db.UniqueConstraint(
            'job_id', 'detail_no', name='uq_job_detail_no'
        ),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id = db.Column(
        db.String(20), db.ForeignKey('job_master.job_no', ondelete='CASCADE'),
        nullable=False, index=True
    )
    detail_no = db.Column(db.Integer, nullable=False)


    # Legacy imposition (Unit 2); Unit 1 uses yield_loss_pct for RM gross-up.
    ups = db.Column(db.Integer, nullable=True)
    yield_loss_pct = db.Column(db.Numeric(5, 2), nullable=True, default=2)
    element_name = db.Column(db.String(100), nullable=True)

    # Paper / raw material
    raw_material_item_code = db.Column(db.String(50), nullable=True)
    paper_brand = db.Column(db.String(100), nullable=True)
    mill = db.Column(db.String(100), nullable=True)
    total_sheets = db.Column(db.Integer, nullable=True)
    paper_supplied_by = db.Column(
        db.Enum('customer', 'company'), default='company', nullable=True
    )
    wastage_pct = db.Column(db.Numeric(5, 2), default=0, nullable=True)
    # wastage_sheets is a stored computed value — call compute_wastage() before save
    wastage_sheets = db.Column(db.Integer, default=0, nullable=True)

    # Sheet dimensions
    sheet_length = db.Column(db.Numeric(8, 2), nullable=True)
    sheet_width = db.Column(db.Numeric(8, 2), nullable=True)
    gsm = db.Column(db.Integer, nullable=True)

    # Printing spec
    print_style = db.Column(db.String(50), nullable=True)     # sheetfed / web / roll
    print_type = db.Column(db.String(50), nullable=True)      # offset / digital / flexo
    front_colours = db.Column(db.String(100), nullable=True)   # e.g. CMYK, PMS, hybrid UV notes
    back_colours = db.Column(db.String(100), nullable=True)

    # Post-press & finishing
    die_no = db.Column(db.String(50), nullable=True)
    pasting_style = db.Column(db.String(50), nullable=True)

    # Freetext
    special_instructions = db.Column(db.Text, nullable=True)

    # Relationships
    boms = db.relationship(
        'Bom', backref='detail_line', lazy='dynamic',
        cascade='all, delete-orphan'
    )
    fg_involved = db.relationship(
        'JobDetailLineFgInvolved', backref='detail_line', lazy='dynamic',
        cascade='all, delete-orphan',
        foreign_keys='JobDetailLineFgInvolved.detail_line_id',
    )

    @property
    def active_bom(self):
        """Return the single active BOM for this detail line, or None."""
        return self.boms.filter_by(is_active=True).first()

    # -------------------------------------------------------------- helpers
    def compute_wastage(self) -> None:
        """Recalculate wastage_sheets from total_sheets and wastage_pct.

        Call this before db.session.add() whenever either field changes.
        Uses math.ceil because we always round up to whole sheets.
        """
        # When `wastage_pct` is NULL, the UI is treating `wastage_sheets` as a manual absolute
        # entry (e.g. edit-BOM "Wastage sheets"). In that mode, do not recompute or overwrite it.
        if self.wastage_pct is None:
            if self.wastage_sheets is None:
                self.wastage_sheets = 0
            return
        # In this app, `total_sheets` is treated as **gross** sheets (net + wastage).
        # The UI captures wastage_pct as a percentage of **net** sheets (qty/ups).
        #
        # If:
        #   gross = net + net * p/100 = net * (100+p)/100
        # then:
        #   wastage = gross - net = gross * p/(100+p)
        if self.total_sheets and self.wastage_pct:
            try:
                gross = float(self.total_sheets)
                p = float(self.wastage_pct)
            except (TypeError, ValueError):
                self.wastage_sheets = 0
                return
            if gross > 0 and p > 0:
                self.wastage_sheets = int(math.ceil(gross * p / (100.0 + p)))
            else:
                self.wastage_sheets = 0
        else:
            self.wastage_sheets = 0

    @property
    def total_sheets_with_wastage(self) -> int:
        """Gross sheet quantity = net + wastage. Used for BOM autofill."""
        ts = self.total_sheets or 0
        ws = self.wastage_sheets or 0
        return ts + ws

    def __repr__(self) -> str:
        return f'<DetailLine job={self.job_id} D{self.detail_no}>'


# ------------------------------------------- JobDetailLineFgInvolved
class JobDetailLineFgInvolved(db.Model):
    """Links a detail (printing) line to one or more header FG/SO lines.

    One row per selected FG. Denormalized SO/FG fields support reporting without joins.
    """

    __tablename__ = 'job_detail_line_fg_involved'
    __table_args__ = (
        db.UniqueConstraint(
            'detail_line_id', 'header_line_id',
            name='uq_job_detail_fg_header',
        ),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id = db.Column(
        db.String(20), db.ForeignKey('job_master.job_no', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    detail_line_id = db.Column(
        db.Integer, db.ForeignKey('job_detail_line.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    header_line_id = db.Column(
        db.Integer, db.ForeignKey('job_header_line.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )

    fg_num = db.Column(db.String(40), nullable=False)
    sap_so_number = db.Column(db.String(30), nullable=True)
    sap_so_doc_entry = db.Column(db.Integer, nullable=True)
    sap_so_line_num = db.Column(db.Integer, nullable=True)
    sap_fg_item_code = db.Column(db.String(80), nullable=True)

    header_line = db.relationship(
        'JobHeaderLine', foreign_keys=[header_line_id],
    )

    @staticmethod
    def fg_num_from_code(fg_code: str) -> str:
        if not fg_code:
            return 'FG'
        match = re.search(r'(FG\d+)', fg_code, re.IGNORECASE)
        return match.group(1).upper() if match else fg_code.strip()

    def __repr__(self) -> str:
        return f'<DetailFgInvolved detail={self.detail_line_id} fg={self.fg_num}>'
