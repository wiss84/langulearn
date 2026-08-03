"""Scenario registry - each sibling module is a self-contained scenario
(TEMPLATE/LABEL/DESCRIPTION), aggregated here into the lookup structures
live_session.py and routes_api.py actually use. Adding a new scenario means
adding one file here (with TEMPLATE/LABEL/DESCRIPTION) and one line in
_MODULES below - nothing else in this package needs to change. The
correction-mandatory rules, difficulty phrasing, and mood tool instruction
are NOT part of any scenario file - they're shared across every scenario
and appended by build_config (live_session.py) after the scenario's own
TEMPLATE, see constants.py's CORE_TUTOR_RULES / DIFFICULTY_INSTRUCTIONS.
"""

from . import airport, cafe, directions, free_learning, letters_and_numbers, restaurant

_MODULES = {
    "free_learning": free_learning,
    "restaurant": restaurant,
    "cafe": cafe,
    "airport": airport,
    "directions": directions,
    "letters_and_numbers": letters_and_numbers,
}

DEFAULT_SCENARIO = "free_learning"

SCENARIO_TEMPLATES = {sid: mod.TEMPLATE for sid, mod in _MODULES.items()}

SCENARIO_OPTIONS = [{"id": sid, "label": mod.LABEL, "description": mod.DESCRIPTION} for sid, mod in _MODULES.items()]
