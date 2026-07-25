"""Assignment scenario 5 (contextual memory): "our usual sync-up" - must recall a previously
established default duration rather than asking again or guessing."""

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import UnresolvedReferenceError, resolve_contextual_duration  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import ContextualReference  # noqa: E402


def test_contextual_reference_extracted_correctly_from_mock_llm():
    intent = extract_intent("our usual sync-up")
    assert isinstance(intent, ContextualReference)
    assert intent.reference == "our usual sync-up"


def test_contextual_reference_recalls_a_known_default_without_asking():
    intent = extract_intent("our usual sync-up")
    duration = resolve_contextual_duration(intent, known_defaults={"usual sync-up": 30})
    assert duration == 30


def test_contextual_reference_matches_partial_phrasing():
    """The remembered key and the referenced phrase don't have to match exactly - "our usual
    sync-up" should still match a remembered "usual sync-up" default."""
    intent = extract_intent("our usual sync-up")
    duration = resolve_contextual_duration(intent, known_defaults={"usual sync-up": 45})
    assert duration == 45


def test_contextual_reference_unknown_raises_instead_of_guessing():
    """No remembered default yet - must surface as a clarifying question upstream (see
    app/dialogue/manager.py's pending_contextual_reference flow), never a guessed duration."""
    intent = extract_intent("our usual sync-up")
    try:
        resolve_contextual_duration(intent, known_defaults={})
        assert False, "expected UnresolvedReferenceError"
    except UnresolvedReferenceError:
        pass
