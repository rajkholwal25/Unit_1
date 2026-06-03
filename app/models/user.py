from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db

USER_ROLES = ('admin', 'manager', 'planner', 'operator', 'quality', 'viewer')
USER_ROLE_LABELS = {
    'admin': 'Admin',
    'manager': 'Manager',
    'planner': 'Planner',
    'operator': 'Operator',
    'quality': 'Quality',
    'viewer': 'Viewer',
}
USER_ROLE_CHOICES = [(r, USER_ROLE_LABELS[r]) for r in USER_ROLES]


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=True, index=True)
    email = db.Column(db.String(256), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(32), nullable=False, default='viewer')
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user is not False

    def has_role(self, *roles: str) -> bool:
        if self.role == 'manager' and 'admin' in roles:
            return True
        if self.role == 'admin' and 'manager' in roles:
            return True
        return self.role in roles

    @property
    def is_admin(self):
        return self.role in ('admin', 'manager')

    def is_manager(self):
        return self.role in ('manager', 'admin')

    def is_planner(self):
        return self.role in ('planner', 'admin', 'manager')

    def is_viewer(self):
        return self.role == 'viewer'

    def can_change_job_status(self):
        return self.role in ('admin', 'manager', 'planner', 'quality')

    def can_edit_job_card(self):
        return self.role in ('admin', 'manager', 'operator', 'planner')

    def can_delete_job_card(self):
        return self.role in ('admin', 'manager')

    def can_push_to_sap(self):
        return self.role in ('admin', 'manager')

    def can_manage_users(self):
        return self.role in ('admin', 'manager')
