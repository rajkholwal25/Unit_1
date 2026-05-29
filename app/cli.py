from .extensions import db
from .models import MaterialType, BomTemplate, Pattern

def seed_defaults(app):
    with app.app_context():
        if not MaterialType.query.first():
            for code,name in [('PET','PET Film'),('BOPP','BOPP Film'),('PVC','PVC Sheet')]:
                db.session.add(MaterialType(code=code,name=name))
        if not BomTemplate.query.first():
            templates = [
                ('EMB→MET→SLT',['EMB','MET','SLT']),
                ('EMB→SLT',['EMB','SLT']),
                ('EMB→HRI→SLT',['EMB','HRI','SLT'])
            ]
            for n,seq in templates:
                db.session.add(BomTemplate(template_name=n, process_sequence=seq))
        db.session.commit()
