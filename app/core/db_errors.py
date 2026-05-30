from sqlalchemy.exc import IntegrityError

from ..extensions import db


def safe_commit_delete(instance, *, in_use_message=None):
    """
    Delete a model row and commit. On FK violation, roll back and return a user message.
    Returns (success: bool, error: str | None).
    """
    try:
        db.session.delete(instance)
        db.session.commit()
        return True, None
    except IntegrityError:
        db.session.rollback()
        return False, in_use_message or (
            'Cannot delete: this record is still used by generated item codes.'
        )
