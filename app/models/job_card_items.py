from datetime import datetime

from app.extensions import db


class JobCardMaterial(db.Model):
    __tablename__ = 'job_card_materials'

    id = db.Column(db.Integer, primary_key=True)
    job_card_id = db.Column(db.Integer, db.ForeignKey('job_cards.id'), nullable=False)
    material_name = db.Column(db.String(200), nullable=False)
    material_code = db.Column(db.String(50), nullable=True)
    paper_type = db.Column(db.String(100), nullable=True)
    gsm = db.Column(db.String(20), nullable=True)
    width_mm = db.Column(db.Float, nullable=True)
    height_mm = db.Column(db.Float, nullable=True)
    length_mm = db.Column(db.Float, nullable=True)
    ink_colors = db.Column(db.String(200), nullable=True)
    quantity_required = db.Column(db.Float, nullable=True, default=0)
    uom = db.Column(db.String(20), nullable=True, default='PCS')
    remarks = db.Column(db.Text, nullable=True)

    num_ups = db.Column(db.Integer, nullable=True)
    element_name = db.Column(db.String(200), nullable=True)
    raw_material_item_code = db.Column(db.String(50), nullable=True)
    paper_brand = db.Column(db.String(200), nullable=True)
    total_sheets = db.Column(db.Float, nullable=True)
    paper_supplied_by = db.Column(db.String(50), nullable=True)
    wastage_pct = db.Column(db.Float, nullable=True)
    wastage_sheets = db.Column(db.Float, nullable=True)
    print_style = db.Column(db.String(80), nullable=True)
    mill = db.Column(db.String(200), nullable=True)
    detail_special_instructions = db.Column(db.Text, nullable=True)
    die_no = db.Column(db.String(100), nullable=True)
    front_colours = db.Column(db.String(200), nullable=True)
    back_colours = db.Column(db.String(200), nullable=True)
    pasting_style = db.Column(db.String(200), nullable=True)
    print_type_metpet = db.Column(db.String(100), nullable=True)

    def __repr__(self):
        return f'<JobCardMaterial {self.material_name}>'


class JobCardPrintingSpec(db.Model):
    __tablename__ = 'job_card_printing_specs'

    id = db.Column(db.Integer, primary_key=True)
    job_card_id = db.Column(db.Integer, db.ForeignKey('job_cards.id'), nullable=False, unique=True)
    plate_size = db.Column(db.String(50), nullable=True)
    number_of_colors = db.Column(db.Integer, nullable=True, default=0)
    printing_type = db.Column(db.String(50), nullable=True)
    finishing_type = db.Column(db.String(100), nullable=True)
    lamination_type = db.Column(db.String(50), nullable=True)
    cutting_type = db.Column(db.String(100), nullable=True)
    special_instructions = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<JobCardPrintingSpec for JC#{self.job_card_id}>'


class JobCardStatusHistory(db.Model):
    __tablename__ = 'job_card_status_history'

    id = db.Column(db.Integer, primary_key=True)
    job_card_id = db.Column(db.Integer, db.ForeignKey('job_cards.id'), nullable=False)
    old_status = db.Column(db.String(20), nullable=True)
    new_status = db.Column(db.String(20), nullable=False)
    changed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    remarks = db.Column(db.Text, nullable=True)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)

    changer = db.relationship('User', backref='status_changes')

    def __repr__(self):
        return f'<StatusHistory {self.old_status} -> {self.new_status}>'
