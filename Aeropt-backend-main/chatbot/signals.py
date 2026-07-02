from django.dispatch import Signal

# Sent after assistant response is persisted.
# Payload: user, session, message, concepts_data
concept_mentioned = Signal()
