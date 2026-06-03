from ..extensions import db
from ..models import User


def ensure_default_manager(app):
    try:
        with app.app_context():
            user = User.query.filter_by(email='manager@test.com').first()
            if user:
                return
            user = User(
                email='manager@test.com',
                username='manager',
                role='manager',
            )
            user.set_password('test@123')
            db.session.add(user)
            db.session.commit()
            app.logger.info('Created default manager user manager@test.com')
    except Exception:
        pass
