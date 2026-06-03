from app.extensions import db
from app.models.job_card import JobCard
from app.models.job_card_items import JobCardStatusHistory


def change_job_card_status(job_card, new_status, user_id, remarks=None):
    """
    Change job card status with validation and audit trail.
    Returns (success: bool, message: str).
    """
    if not job_card.can_transition_to(new_status):
        return False, f'Cannot transition from "{job_card.status}" to "{new_status}".'

    old_status = job_card.status
    job_card.status = new_status

    history = JobCardStatusHistory(
        job_card_id=job_card.id,
        old_status=old_status,
        new_status=new_status,
        changed_by=user_id,
        remarks=remarks,
    )
    db.session.add(history)
    db.session.commit()

    return True, f'Status changed from "{old_status}" to "{new_status}".'


def get_job_card_summary():
    """Get summary counts for dashboard."""
    return {
        'total': JobCard.query.count(),
        'open': JobCard.query.filter_by(status='open').count(),
        'staged': JobCard.query.filter_by(status='staged').count(),
        'released': JobCard.query.filter_by(status='released').count(),
        'closed': JobCard.query.filter_by(status='closed').count(),
    }
