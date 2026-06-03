# import uuid removed
from app.extensions import db


# ----------------------------------------------------------- ProcessMaster
class ProcessMaster(db.Model):
    """Master list of manufacturing processes for this company.

    process_code is the controlled vocabulary key used in BomStep.
    Seeded via seed_processes.py — planners pick from this list,
    they cannot free-type process codes.

    category groups processes for display:
      pre-press / printing / post-press / converting / finishing / packing
    """
    __tablename__ = 'process_master'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    process_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=True, index=True)
    # Default SAP workcenter resource code for this process
    default_workcenter = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f'<ProcessMaster {self.process_code}: {self.name}>'
