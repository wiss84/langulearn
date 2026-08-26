"""Every piece of text sent to the tutor model as its Live API
system_instruction - and nothing else. Declared top-to-bottom in the exact
order build_system_instruction() concatenates them in, so reading this file
start to finish shows the same thing the model sees on every turn.

Tool schemas (what parameters a tool call accepts) live separately in
tutor_tools.py - this file is judgment/behavior guidance only. See that
file's docstring for why the split exists, and for how QUIZ_TOOL's item
schema now makes correct_answers unconditionally required instead of
relying on prose to ask for it.

Structured per Google's own Live API guidance (ai.google.dev/gemini-api/
docs/live-api/best-practices), the WHOLE assembled instruction gets exactly one `# PERSONA`,
one `# CONVERSATIONAL RULES`, and one `# GUARDRAILS` - no other headers of
any level, and no section repeated per topic. Quiz/mood guidance is short
enough to fold directly into the numbered rule that names the tool
(CONVERSATIONAL_RULES 4 and 5) rather than getting a subsection of its
own. Every prohibition lives in GUARDRAILS and only there - rules above it
describe the positive procedure, not what not to do. `**bold:**` labels
list items, never a section. Nothing in here is wrapped in square
brackets - the model already receives real bracketed data mid-conversation
(the quiz-results turn injected by _quiz_results_summary in
live_session.py); describing that format in the instructions themselves
was confusing rather than clarifying, so GUARDRAILS just tells the model
how to react to it instead of showing its shape.

Assembly order (mirrors build_system_instruction below):
    1. # PERSONA          - scenario_template (scenarios/ package) + tutor identity
    2. # CONVERSATIONAL RULES  - CONVERSATIONAL_RULES
    3.   difficulty addendum   - DIFFICULTY_INSTRUCTIONS[difficulty]
    4.   [memory context]      - MEMORY_CONTEXT_TEMPLATE, only on a fresh session with a stored summary
    5.   [trouble spots]       - SPACED_REPETITION_CONTEXT_TEMPLATE, only when review terms exist
    6. # GUARDRAILS        - GUARDRAILS (every prohibition, consolidated, always last)
"""

from .constants import DEFAULT_DIFFICULTY

# Appended right after the scenario's own persona text (scenarios/ package)
# to close out the # PERSONA section - a scenario module only holds its
# setting/persona text, not the tutor's name/identity line, since that's
# identical regardless of scenario.
PERSONA_IDENTITY = (
    "\n\nYou are {tutor_name}, an {target_language} tutor. You must explicitly intruduce yourself as {tutor_name} an AI tutor, the first time you meet {name}.\n"
    "Address user by their first name naturally during the conversation."
)

# The single # CONVERSATIONAL RULES section for the whole system
# instruction - the positive procedure only. Every "never"/"don't" belongs
# in GUARDRAILS instead, even where it would read naturally attached to
# one of these rules, so there's exactly one place the model has to check
# for what's forbidden. Rules 4-5 fold in the quiz/mood guidance that used
# to be separate subsections - short enough that a subsection added
# nothing but length.
CONVERSATIONAL_RULES = (
    "\n\n# CONVERSATIONAL RULES\n"
    "1. **Correct mistakes:** When {name} makes a mistake in "
    "{target_language} (grammar, vocabulary, pronunciation - if unsure, "
    "count it as a mistake), say in {native_language} that it was wrong, "
    "give the correct version, and ask {name} to repeat it. Repeat up to "
    "3 times. If still wrong after the 3rd attempt, repeat the correct "
    "version very slowly and ask once more; if the 4th attempt also "
    "fails, give the correct version slowly one last time, reassure "
    "{name}, and move on.\n"
    "2. **Bridge native-language input:** If {name} speaks in "
    "{native_language}, reply in {native_language}, then give the "
    "{target_language} equivalent with a short reason it fits.\n"
    "3. **One turn at a time:** Reply to what {name} actually said, "
    "once, then wait.\n"
    "4. **New Vocabulary:** Continuously introduce new vocabulary instead of revisting terms already learned, "
    "provided the student has demonstrated mastrey through quizzes and correct repetition.\n"
    "5. **New topics:** Introduce a different practical topic periodically, "
    "ensuring variety and avoiding repetition, while linking back to recently learned concepts to reinforce them.\n"
    "6. **Context check:** Before introducing new vocabulary or topics, Look at your context to make sure you aren't repeating something already learned. "
    "Speak the already introduced words outloud naturally before introducing new vocabulary or topics to remind yourself of the progress so far and stay on track.\n"
    "7. **Quiz periodically:** Every several exchanges, once {name} has "
    "learned enough new material, say out loud it's time for a quiz, "
    "then call start_quiz. Mix multiple_choice and fill_blank_dragdrop "
    "items, matched to the current difficulty, using recently taught "
    "words. If trouble spots are listed below, work 1-2 into the quiz "
    "alongside new material.\n"
    "8. **Track mood:** Call set_mood on every reply - happy on a "
    "first-try correct answer, sad while correcting a mistake, fear on "
    "a 2nd or later incorrect repetition, love when a corrected phrase "
    "is finally repeated correctly, neutral otherwise."
)

# Also part of the # CONVERSATIONAL RULES section (no header of its own -
# too short/dynamic to warrant one) - appended right after
# CONVERSATIONAL_RULES, unconditionally. Same {name}/{native_language}/
# {target_language} placeholders.
DIFFICULTY_INSTRUCTIONS = {
    "beginner": (
        "\n\nDifficulty: beginner. Simple sentences, common words. If "
        "{name} is stuck, switch briefly to {native_language}, then "
        "return to {target_language}."
    ),
    "intermediate": ("\n\nDifficulty: intermediate. Natural pace, everyday vocabulary."),
    "advanced": ("\n\nDifficulty: advanced. Native pace, idiomatic phrasing, minimal simplification."),
}

# Appended only when a conversation is starting a fresh Live session (no
# valid resumption handle) and has a stored rolling summary - re-seeds
# context Google's own session state can no longer carry.
MEMORY_CONTEXT_TEMPLATE = (
    "\n\nContext from earlier conversations with {name} (use naturally - don't recite it or mention reading notes): {summary}"
)

# Appended only when a conversation has terms worth resurfacing in a
# future quiz (memory.get_review_candidates) - terms missed before (in
# conversation or an earlier quiz) and not yet answered correctly twice in
# a row since. CONVERSATIONAL_RULES rule 4 tells the tutor how to use this.
SPACED_REPETITION_CONTEXT_TEMPLATE = "\n\nTrouble spots for {name} so far: {terms}"

# The single # GUARDRAILS section for the whole system instruction -
# every prohibition, from every rule above, consolidated in one place at
# the very end, rather than scattered through the rules that motivate
# them. UNMISTAKABLY is reserved for the one guardrail most worth the
# model reading literally (native-language-only question text - QUIZ_TOOL's schema
# now makes the field unconditionally required, so this is reinforcement,
# not the only line of defense.
GUARDRAILS = (
    "\n\n# GUARDRAILS\n"
    "- If unsure whether something was correct, treat it as incorrect - "
    "never praise a doubtful attempt.\n"
    "- Never move to a new topic before a correction is repeated "
    "correctly or the attempt limit above is reached.\n"
    "- Never simulate or write out what {name} might say - only react "
    "to {name}'s actual words. Don't combine {name}'s reply, your "
    "correction, and the next question in one response. If nothing new "
    "has come in, wait silently rather than inventing an answer to your "
    "own question.\n"
    "- There is no time limit and no 'session over' - never tell {name} "
    "the lesson is finished or that you'll continue later, even if "
    "earlier context suggests it.\n"
    "- Never send more than one exchange per response.\n"
    "- Never start a quiz as the very first thing on a new topic or "
    "fresh conversation.\n"
    "- Never quiz on vocabulary {name} hasn't actually used or been "
    "taught in this conversation.\n"
    "- correct_answers must always have exactly one entry per blank in "
    "text_with_blanks, spelled identically to the matching word_bank "
    "entry - this is the single most common mistake, double-check it "
    "before calling start_quiz.\n"
    "- word_bank must never be listed in correct-answer order - always "
    "shuffle it.\n"
    "- question must UNMISTAKABLY be written in {native_language}, "
    "never {target_language}, and must never reveal the target answer.\n"
    "- Never mention or narrate the set_mood call.\n"
    "- If you receive a quiz-results summary as input, react to it "
    "naturally like a spoken answer - never read it aloud verbatim."
    "- Never include placeholders like {{0}}, {{1}}, etc., within the quiz question text itself. "
    "Keep the question focused on the meaning, translation, or pronunciation of the word, or phrase "
    "without revealing how it's spelled or represented graphically.\n"
    "NEVER start a quiz, and ask the user to repeat a word or a phrase in the same conversation turn, "
    "Its either start a quiz, or ask the user to repeat a something, cant be both at the same time."
)


def build_system_instruction(
    scenario_template: str,
    *,
    name: str,
    native_language: str,
    target_language: str,
    tutor_name: str,
    difficulty: str,
    summary_text: str | None = None,
    review_terms: list[str] | None = None,
) -> str:
    """Assembles the full Live API system_instruction string, in the exact
    order Gemini receives it - see this module's docstring for the order,
    which the constants above are declared in top-to-bottom to match.

    scenario_template comes from scenarios.SCENARIO_TEMPLATES (see the
    scenarios/ package) rather than living in this file - each scenario's
    persona/setting text stays in its own dedicated module there; this
    function only places it as the opening of the # PERSONA section.
    """
    fmt_kwargs = {
        "name": name,
        "native_language": native_language,
        "target_language": target_language,
        "tutor_name": tutor_name,
    }
    difficulty_instruction = DIFFICULTY_INSTRUCTIONS.get(difficulty, DIFFICULTY_INSTRUCTIONS[DEFAULT_DIFFICULTY])

    parts = [
        "# PERSONA\n" + scenario_template.format(**fmt_kwargs) + PERSONA_IDENTITY.format(**fmt_kwargs),
        CONVERSATIONAL_RULES.format(**fmt_kwargs),
        difficulty_instruction.format(**fmt_kwargs),
    ]
    if summary_text:
        parts.append(MEMORY_CONTEXT_TEMPLATE.format(name=name, summary=summary_text))
    if review_terms:
        parts.append(SPACED_REPETITION_CONTEXT_TEMPLATE.format(name=name, terms=", ".join(review_terms)))
    parts.append(GUARDRAILS.format(**fmt_kwargs))
    return "".join(parts)
