"""Run a source-grounded check without Django, uploads or automatic downloads."""
import argparse
import json
from pathlib import Path
import sys
from ml.remote_policy.core import Candidate
from ml.remote_policy.pipeline import PolicyEvaluator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--text'); parser.add_argument('--file', type=Path)
    parser.add_argument('--country', required=True); parser.add_argument('--location', default='')
    parser.add_argument('--state'); parser.add_argument('--utc-offset', type=float)
    parser.add_argument('--authorized-in', action='append', default=None)
    parser.add_argument('--known-no-authorization', action='store_true')
    parser.add_argument('--employment-type', choices=['employee', 'contractor'])
    parser.add_argument('--local-llm', help='Explicit installed Ollama model, e.g. qwen3.5:4b')
    parser.add_argument('--deepseek', action='store_true', help='Explicitly allow sending unresolved input to DeepSeek using DEEPSEEK_API_KEY')
    args = parser.parse_args()
    if args.text is not None and args.file: parser.error('Use either --text or --file')
    if args.deepseek and args.local_llm: parser.error('Choose one language-model fallback')
    text = args.file.read_text() if args.file else (args.text if args.text is not None else sys.stdin.read())
    if args.known_no_authorization and args.authorized_in: parser.error('Conflicting authorization inputs')
    auth = () if args.known_no_authorization else (tuple(args.authorized_in) if args.authorized_in else None)
    try:
        candidate = Candidate(args.country, args.state, args.utc_offset, auth, args.employment_type)
        llm = None
        if args.local_llm:
            from ml.remote_policy.llm import LocalLLM
            llm = LocalLLM(args.local_llm)
        elif args.deepseek:
            from ml.remote_policy.cloud import DeepSeekLLM
            llm = DeepSeekLLM()
        result = PolicyEvaluator(llm).evaluate(text, candidate, location=args.location)
    except ValueError as exc: parser.error(str(exc))
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__': main()
