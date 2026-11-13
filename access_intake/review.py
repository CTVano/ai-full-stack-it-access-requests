"""Intake records carry review instructions, never access authorizations."""
from .privacy import SafeError


APPROVAL_BOUNDARY = (
    'Intake completion does not approve access. This application prepares a review queue. '
    'A named manager is a contact. The System Owner makes the access decision; '
    'Security Review first assesses risk and forwards its recommendation when the policy requires it.'
)

ROUTES = {
    ('review','IT Service Desk','inspect-message'),
    ('review','IT Service Desk','confirm-withdrawal'),
    ('needs-information','IT Service Desk','clarify'),
    ('review','Security Review','review-risk-then-owner'),
    ('review','System Owner','review-request'),
    ('error','IT Service Desk','retry-extraction'),
}

NOTES = {
    'review-request':'Intake completed; owner decision still outstanding.',
    'review-risk-then-owner':'Intake completed; risk recommendation and subsequent owner decision still outstanding.',
    'inspect-message':'Intake completed; message inspection is outstanding before any access decision.',
    'confirm-withdrawal':'Intake completed; human withdrawal confirmation is outstanding.',
    'clarify':'Intake completed; clarification is required before an access decision.',
    'retry-extraction':'Intake failed; retry extraction. No access decision has been made.',
}


def review_record(record):
    routing = tuple(record.get(key) for key in ['status','owner','next_action'])
    if (routing not in ROUTES
            or ('access_granted' in record and record['access_granted'] is not False)
            or ('access_decision' in record and record['access_decision'] != 'outstanding')):
        raise SafeError('approval boundary: intake may only prepare review outcomes; it cannot approve or grant access')
    return dict(record,
                intake_status='error' if record['status']=='error' else 'completed',
                access_decision='outstanding',decision_owner='System Owner',access_granted=False,
                review_note=NOTES[record['next_action']])
