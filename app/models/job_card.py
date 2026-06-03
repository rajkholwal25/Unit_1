from datetime import datetime, date

from app.extensions import db

STATUSES = ('open', 'staged', 'released', 'closed', 'cancelled')
PRIORITIES = ('low', 'medium', 'high', 'urgent')

VALID_TRANSITIONS = {
    'open': ['staged', 'closed', 'cancelled'],
    'staged': ['released', 'open', 'closed', 'cancelled'],
    'released': ['closed', 'cancelled'],
    'closed': [],
    'cancelled': [],
}


class JobCard(db.Model):
    __tablename__ = 'job_cards'

    id = db.Column(db.Integer, primary_key=True)
    job_card_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    sap_customer_code = db.Column(db.String(30), db.ForeignKey('sap_customer_mirror.card_code'), nullable=False)
    product_name = db.Column(db.String(200), nullable=False)
    product_description = db.Column(db.Text, nullable=True)
    item_code = db.Column(db.String(50), nullable=True)
    quantity = db.Column(db.Float, nullable=False, default=0)
    uom = db.Column(db.String(20), nullable=True, default='PCS')
    delivery_date = db.Column(db.Date, nullable=True)
    priority = db.Column(db.Enum(*PRIORITIES, name='priority_type'), nullable=False, default='medium')
    status = db.Column(db.String(20), nullable=False, default='open')

    sap_production_order = db.Column(db.String(50), nullable=True)
    sap_bom_number = db.Column(db.String(50), nullable=True)

    sap_so_doc_num = db.Column(db.String(50), nullable=True)
    sap_so_doc_entry = db.Column(db.Integer, nullable=True)
    sap_mjd1_line_code = db.Column(db.String(120), nullable=True)
    sap_fg_code = db.Column(db.String(100), nullable=True)
    sap_fg_name_snap = db.Column(db.String(200), nullable=True)
    sap_selected_lines_json = db.Column(db.Text, nullable=True)
    process_sequence_json = db.Column(db.Text, nullable=True)
    carton_length_mm = db.Column(db.Float, nullable=True)
    carton_width_mm = db.Column(db.Float, nullable=True)
    carton_height_mm = db.Column(db.Float, nullable=True)
    job_kind = db.Column(db.String(20), nullable=True)
    sap_po_doc_entries_json = db.Column(db.Text, nullable=True)

    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    materials = db.relationship('JobCardMaterial', backref='job_card', lazy='dynamic',
                                cascade='all, delete-orphan')
    printing_spec = db.relationship('JobCardPrintingSpec', backref='job_card', uselist=False,
                                    cascade='all, delete-orphan')
    status_history = db.relationship('JobCardStatusHistory', backref='job_card', lazy='dynamic',
                                     cascade='all, delete-orphan', order_by='JobCardStatusHistory.changed_at.desc()')

    @property
    def is_synced_to_sap(self):
        return bool(self.sap_production_order)

    def can_transition_to(self, new_status):
        return new_status in VALID_TRANSITIONS.get(self.status, [])

    @staticmethod
    def generate_number():
        """Generate next job card number: JC-YYYY-XXXXX"""
        year = date.today().year
        prefix = f'JC-{year}-'
        last = JobCard.query.filter(
            JobCard.job_card_number.like(f'{prefix}%')
        ).order_by(JobCard.id.desc()).first()

        if last:
            last_num = int(last.job_card_number.split('-')[-1])
            next_num = last_num + 1
        else:
            next_num = 1

        return f'{prefix}{next_num:05d}'

    def __repr__(self):
        return f'<JobCard {self.job_card_number}>'
