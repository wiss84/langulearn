"""Free Learning scenario - the default, general-purpose tutor persona (no
roleplay setting). This is what every conversation used before scenarios
existed, minus the correction rules and difficulty/mood handling, which
are now shared across every scenario (see constants.py's CORE_TUTOR_RULES /
DIFFICULTY_INSTRUCTIONS and live_session.py's MOOD_INSTRUCTION) rather than
duplicated per scenario file.
"""

LABEL = "Free Learning"
DESCRIPTION = "Open-ended conversation practice - no fixed setting."

TEMPLATE = (
    "You are a strict but encouraging {target_language} tutor for {name}, "
    "a {native_language} speaker. Speak mainly in {native_language}; never reply "
    "entirely in {target_language}."
)
