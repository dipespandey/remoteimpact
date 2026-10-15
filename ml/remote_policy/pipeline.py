"""Inspectable rules-first policy evaluator; optional explicit local LLM fallback."""
import re
from ml.remote_policy.core import Decision, rules, place_codes


def missing_details(decision, candidate):
    """A language model cannot manufacture missing applicant facts."""
    for item in decision.evidence:
        condition = item['condition']
        if condition == 'Stated work authorization requirement.' and candidate.authorized_countries is None:
            return True
        if condition == 'Stated US state restriction.' and candidate.country == 'US' and candidate.subdivision is None:
            return True
        if condition == 'Stated residential UTC-offset range.' and candidate.utc_offset is None:
            return True
        if condition in {
            'Working-hour overlap is not implied by residential timezone.',
            'Timezone or working-hour condition cannot be resolved from supplied details.',
            'Mandatory workplace attendance is outside country-only eligibility.',
            'Required travel availability was not supplied.',
            'Georgia may refer to a country or a US state.',
            'Input exceeds the supported 100,000-character limit.',
            'Multiple geographic references have different possible scopes.',
            'Mixed geographic inclusion and exclusion need scoped parsing.',
            'The source does not establish a currently remote position.',
        }:
            return True
        if condition == 'Conditional policy needs interpretation.' and candidate.employment_type is None:
            return True
        if condition.startswith('Citizenship, clearance') and any(x in item['quote'].lower() for x in ['citizenship', 'citizen', 'passport', 'clearance']):
            return True
        if condition.startswith('Citizenship, clearance') and 'unless' in item['quote'].lower():
            exception = item['quote'].lower().split('unless', 1)[1]
            if candidate.country in place_codes(exception):
                return True
    return False


def should_fallback(rule_decision, candidate, text=''):
    if rule_decision.label != 'unknown' or missing_details(rule_decision, candidate):
        return False
    if rule_decision.evidence:
        return True
    # Generic 'remote' plus a headquarters/office location is not an invitation
    # to have a model guess. Require an unresolved applicant-facing policy.
    return bool(re.search(r'\b(?:you|your|applicants?|candidates?|eligib\w*|residen\w*|home|live|living|work from)\b', text, re.I))


def combine(rule_decision, llm_decision, candidate, text=''):
    if not should_fallback(rule_decision, candidate, text):
        return rule_decision
    llm_decision.backend = 'rules+' + llm_decision.backend
    return llm_decision


class PolicyEvaluator:
    def __init__(self, llm=None):
        # No downloads, subprocesses, provider calls or model loading by default.
        self.llm = llm

    def evaluate(self, text, candidate, *, location=''):
        decision = rules(text, candidate, location=location)
        if self.llm is None or not should_fallback(decision, candidate, text):
            return decision
        try:
            generated, _ = self.llm.predict(text, candidate, location=location)
            return combine(decision, generated, candidate, text)
        except Exception:
            decision.reasons.append('Local language model unavailable; retained unknown.')
            return decision
