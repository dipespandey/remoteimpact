"""Pinned acquisition and group-separated, explicitly weak-labelled training data."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import re

import requests
from ml.remote_policy.core import Candidate, EU, EUROPE, clean, place_codes

DATASET = 'jobicy/remote-jobs'
REVISION = '38b2307f0dc34786a46041c0c4455b61bbc1a8dd'
ROOT = Path('ml/data/remote-policy')
COUNTRIES = ['US', 'CA', 'GB', 'DE', 'FR', 'NO', 'NP', 'IN', 'AU', 'BR', 'MX', 'KE', 'JP', 'NL', 'PL', 'ES', 'IE', 'PH', 'SG', 'ZA']
PLACES = [('United States', {'US'}), ('Canada', {'CA'}), ('United Kingdom', {'GB'}),
          ('Germany', {'DE'}), ('Norway', {'NO'}), ('Nepal', {'NP'}), ('India', {'IN'}),
          ('Australia', {'AU'}), ('Brazil', {'BR'}), ('Mexico', {'MX'}), ('Kenya', {'KE'}),
          ('Europe', EUROPE), ('European Union', EU), ('EEA', EU | {'NO', 'LI', 'IS'}),
          ('United States or Canada', {'US', 'CA'}), ('Germany, France or the Netherlands', {'DE', 'FR', 'NL'})]

# Entire wording families belong to exactly one split. All labels below are
# programmatic labels of original synthetic policies, not human-reviewed truth.
TEMPLATES = {
    'train': [
        ('Applicants must be based in {p}.', 'include'),
        ('This position is remote within {p} only.', 'include'),
        ('We are hiring candidates from {p}.', 'include'),
        ('Location: {p}. Remote work is supported.', 'include'),
        ('Candidates need to reside in {p}.', 'include'),
        ('We only hire residents of {p}.', 'include'),
        ('Remote worldwide. We cannot hire candidates from {p}.', 'exclude'),
        ('Work from anywhere except {p}.', 'exclude'),
        ('Applicants outside {p} are not eligible.', 'include'),
        ('The job is globally remote, with no country restrictions.', 'world'),
        ('Work from any country. Our customers are in {p}.', 'world'),
        ('We are headquartered in {p}. Remote work available.', 'unknown'),
        ('We serve clients in {p}. The role is remote.', 'unknown'),
        ('Remote. Pay ranges below describe employees in {p}.', 'unknown'),
        ('Remote. Annual travel to {p} is expected.', 'unknown'),
        ('Work location will be agreed with the recruiter.', 'unknown'),
        ('Applicants must already be authorized to work in {p}. Remote worldwide.', 'auth'),
        ('Work from anywhere. You must reside between UTC-3 and UTC+3.', 'offset'),
        ('Work from anywhere. You must overlap four hours with US business hours.', 'unknown'),
        ('This is a remote role. We offer health insurance in {p}.', 'unknown'),
    ],
    'dev': [
        ('Applications are open to people living in {p}.', 'include'),
        ('This vacancy is available exclusively to residents of {p}.', 'include'),
        ('Anywhere in the world, excluding {p}.', 'exclude'),
        ('Worldwide applicants are welcome; no relocation is required.', 'world'),
        ('Our offices are in {p}; this position is remote.', 'unknown'),
        ('You must have the right to work in {p}. Remote worldwide.', 'auth'),
        ('Worldwide remote. Your residential timezone must fall between UTC-3 to UTC+3.', 'offset'),
        ('Remote work is possible, subject to approval.', 'unknown'),
    ],
    'test': [
        ('For this remote opening, eligibility is limited to {p}.', 'include'),
        ('You can perform this job remotely provided your home is in {p}.', 'include'),
        ('We accept remote applications worldwide, but not from {p}.', 'exclude'),
        ('No matter which country you call home, you can work remotely with us.', 'world'),
        ('Our largest market is {p}. Join our remote team.', 'unknown'),
        ('This is fully remote; salaries for {p} are listed separately.', 'unknown'),
        ('This role requires existing work authorization in {p}. Remote worldwide.', 'auth'),
        ('Work from anywhere, provided your home timezone is UTC-3 through UTC+3.', 'offset'),
        ('The remote location policy will be clarified after interview.', 'unknown'),
    ],
}


def read_jsonl(path):
    # splitlines() also splits U+2028 inside valid JSON strings.
    return [json.loads(line) for line in Path(path).read_text().split('\n') if line.strip()]


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in rows))


def acquire(root):
    raw = root / 'raw'; raw.mkdir(parents=True, exist_ok=True)
    response = requests.get(f'https://huggingface.co/api/datasets/{DATASET}/tree/{REVISION}/data/daily', timeout=30)
    response.raise_for_status()
    paths = [x['path'] for x in response.json() if x['path'].endswith('.jsonl')]
    def download(path):
        target = raw / Path(path).name
        if not target.exists():
            r = requests.get(f'https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{path}', timeout=60)
            r.raise_for_status(); target.write_bytes(r.content)
        return {'path': path, 'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(download, sorted(paths)))
    for name in ('README.md', 'LICENSE'):
        r = requests.get(f'https://huggingface.co/datasets/{DATASET}/raw/{REVISION}/{name}', timeout=30)
        r.raise_for_status(); (raw / name).write_text(r.text)
    jobs = {}
    for path in sorted(raw.glob('2026-*.jsonl')):
        for row in read_jsonl(path): jobs[str(row['id'])] = row
    write_jsonl(raw / 'jobs.jsonl', jobs.values())
    manifest = {'dataset': DATASET, 'revision': REVISION, 'files': files, 'unique_jobs': len(jobs),
                'raw_redistribution': False, 'third_party_text_rights': 'Not granted by Jobicy dataset terms.'}
    (raw / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'files': len(files), 'unique_jobs': len(jobs)}), flush=True)


def synthetic():
    from dataclasses import asdict
    rng = random.Random(412)
    for split, templates in TEMPLATES.items():
        rows = []
        for family, (template, kind) in enumerate(templates):
            for place_index, (name, codes) in enumerate(PLACES):
                for code in COUNTRIES:
                    auth = None
                    offset = None
                    label = 'unknown'
                    if kind == 'include': label = 'compatible' if code in codes else 'incompatible'
                    if kind == 'exclude': label = 'incompatible' if code in codes else 'compatible'
                    if kind == 'world': label = 'compatible'
                    if kind == 'auth':
                        # Unknown differs from a known empty authorization set.
                        variant = COUNTRIES.index(code) % 3
                        from ml.remote_policy.core import COUNTRY_NAMES
                        auth = None if variant == 0 else (tuple(sorted(codes & COUNTRY_NAMES.keys())) if variant == 1 else ())
                        label = 'unknown' if auth is None else ('compatible' if auth else 'incompatible')
                    if kind == 'offset':
                        offset = [None, -8, -3, 0, 1, 3, 5.75, 10][COUNTRIES.index(code) % 8]
                        label = 'unknown' if offset is None else ('compatible' if -3 <= offset <= 3 else 'incompatible')
                    candidate = Candidate(code, authorized_countries=auth, utc_offset=offset)
                    text = template.format(p=name)
                    # Independent neutral wrappers vary by split, not shared exact text.
                    if split == 'train' and rng.random() < .5:
                        text = 'We build software for environmental research. ' + text + ' You will collaborate with product colleagues.'
                    rows.append({'id': f'syn-{split}-{family}-{place_index}-{code}', 'group': f'syn-{split}-{family}',
                                 'text': text, 'candidate': asdict(candidate), 'label': label,
                                 'source': 'original_synthetic', 'label_origin': 'template_semantics', 'family': kind})
        # Bound duplication for place-independent policies while preserving balance.
        unique = {(r['text'], json.dumps(r['candidate'], sort_keys=True)): r for r in rows}
        yield split, list(unique.values())


def prepare(root):
    from dataclasses import asdict
    jobs = read_jsonl(root / 'raw/jobs.jsonl')
    # Entire employers are reserved before producing any training examples.
    def split_for(company):
        n = int(hashlib.sha256(company.casefold().encode()).hexdigest()[:8], 16) % 10
        return 'test' if n == 0 else ('dev' if n == 1 else 'train')
    synthetic_rows = dict(synthetic())
    public = {split: [] for split in synthetic_rows}
    for job in jobs:
        geo = clean(job.get('jobGeo', ''))
        codes = place_codes(geo)
        worldwide = geo.lower() in {'anywhere', 'worldwide', 'global'}
        if not codes and not worldwide: continue
        split = split_for(job.get('companyName', ''))
        # Metadata-only supervision: do not present it as full-posting eligibility.
        selected = sorted(codes & set(COUNTRIES))[:1] + [x for x in COUNTRIES if x not in codes][:1]
        if worldwide: selected = ['NP', 'US']
        for code in selected:
            public[split].append({'id': f'jobicy-{job["id"]}-{code}', 'group': 'employer:' + job.get('companyName', '').casefold(),
                                  'text': f'Location: {geo}', 'candidate': asdict(Candidate(code)),
                                  'label': 'compatible' if worldwide or code in codes else 'incompatible',
                                  'source': DATASET, 'source_id': job['id'], 'source_url': job['url'],
                                  'label_origin': 'weak_structured_geography', 'family': 'metadata_only'})
    for split in synthetic_rows:
        # Deduplicate same employer/location/candidate within each partition.
        public[split] = list({(r['group'], r['text'], r['candidate']['country']): r for r in public[split]}.values())
        rows = synthetic_rows[split] + public[split]
        random.Random(412).shuffle(rows)
        write_jsonl(root / f'{split}.jsonl', rows)
    # The same metadata policy appears at many employers. Employer separation
    # alone would leak exact inputs; reserve shared inputs for evaluation instead.
    seen = set()
    final_counts = {}
    for split in ('test', 'dev', 'train'):
        kept = []
        for row in read_jsonl(root / f'{split}.jsonl'):
            key = hashlib.sha256(json.dumps([row['text'], row['candidate']], sort_keys=True).encode()).hexdigest()
            if key in seen: continue
            kept.append(row)
        seen.update(hashlib.sha256(json.dumps([r['text'], r['candidate']], sort_keys=True).encode()).hexdigest() for r in kept)
        write_jsonl(root / f'{split}.jsonl', kept)
        final_counts[split] = Counter(r['source'] for r in kept)
    # Freeze hashes before model fitting. No training routine opens test.jsonl.
    manifest = {'seed': 412, 'source_revision': REVISION, 'independent_human_labels': 0,
                'label_caveat': 'Synthetic semantics and source metadata are weak supervision, not full-posting human gold.',
                'splits': {s: {'rows': sum(final_counts[s].values()), 'synthetic': final_counts[s]['original_synthetic'],
                               'metadata': final_counts[s][DATASET], 'sha256': hashlib.sha256((root / f'{s}.jsonl').read_bytes()).hexdigest()}
                           for s in synthetic_rows}}
    (root / 'split_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['acquire', 'prepare'])
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    (acquire if args.action == 'acquire' else prepare)(args.root)
