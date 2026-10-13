"""Reproducible weak supervision from public jobs; never uses applicant data.

These labels describe broad occupational relevance, not eligibility or a person's
preferences. They are bootstrap labels, not human judgments. Ambiguous titles are
excluded. Organizations and identical content stay in one split.
"""
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

FAMILIES = {
    'software': (r'\b(?:software|backend|back.end|front.end|full.stack|web|mobile|application|platform)\b.{0,24}\b(?:engineer|developer)\b', ['software engineering', 'building software applications', 'backend and web development']),
    'data_engineering': (r'\bdata\s+engineer(?:ing)?\b', ['data engineering', 'building data pipelines and data infrastructure', 'ETL and data platform engineering']),
    'data_science': (r'\bdata\s+scientist\b', ['data science', 'statistical modelling and predictive analytics', 'working as a data scientist']),
    'data_analysis': (r'\b(?:data|business intelligence|analytics)\s+analyst\b', ['data analysis', 'analysing datasets and reporting insights', 'business intelligence and analytics']),
    'policy': (r'\b(?:policy|public policy)\b.{0,30}\b(?:analyst|researcher|advisor|adviser|officer|specialist)\b|\b(?:analyst|researcher)\b.{0,20}\bpolicy\b', ['policy research and analysis', 'analysing public policy and advising decision makers', 'public policy analyst roles']),
    'research': (r'\b(?:research scientist|research fellow|research associate|postdoc|postdoctoral)\b', ['scientific research', 'conducting studies as a research scientist', 'research fellowship roles']),
    'finance': (r'\b(?:financial|finance|investment)\s+(?:analyst|associate)\b', ['financial analysis', 'analysing investments and financial performance', 'finance analyst roles']),
    'accounting': (r'\b(?:accountant|accounting|controller|bookkeeper)\b', ['accounting', 'financial reporting and maintaining accounting records', 'accountant roles']),
    'operations': (r'\boperations\s+(?:manager|coordinator|director|officer|associate|specialist|lead)\b', ['operations management', 'running organisational operations and processes', 'operations coordinator roles']),
    'communications': (r'\b(?:communications?|public relations|copywriter|editor|journalist)\b', ['communications and content', 'writing content and communicating with audiences', 'communications specialist roles']),
    'fundraising': (r'\b(?:fundraising|fundraiser|donor relations|philanthropy)\b', ['fundraising', 'building donor relationships and raising charitable funds', 'fundraising and philanthropy roles']),
    'program_management': (r'\b(?:program|programme|project)\s+(?:manager|coordinator|director|lead|officer)\b', ['programme and project management', 'coordinating programmes and managing delivery', 'project manager roles']),
    'clinical': (r'\b(?:nurse|therapist|clinician|counsellor|counselor)\b', ['clinical care', 'providing direct patient care and therapy', 'nursing and therapy roles']),
    'education': (r'\b(?:teacher|tutor|instructor|instructional designer|curriculum designer)\b', ['teaching and education', 'teaching learners and designing educational content', 'teacher and instructor roles']),
    'legal': (r'\b(?:lawyer|counsel|paralegal|legal researcher|legal advisor|legal adviser|legal officer)\b', ['legal work', 'legal research and advising on law', 'lawyer and legal researcher roles']),
    'hr': (r'\b(?:human resources|recruiter|recruiting|talent acquisition)\b', ['human resources and recruitment', 'recruiting staff and managing HR processes', 'recruiter roles']),
    'support': (r'\bcustomer\s+(?:support|success)\b|\bsupport specialist\b', ['customer support', 'helping customers resolve problems', 'customer support specialist roles']),
    'sales': (r'\b(?:sales|account executive|business development)\b', ['sales and business development', 'selling services and developing customer accounts', 'account executive roles']),
}
PATTERNS = {key: re.compile(value[0], re.I) for key, value in FAMILIES.items()}
NEAR = {'software': ['data_engineering', 'data_science', 'support'], 'data_engineering': ['software', 'data_science', 'data_analysis'], 'data_science': ['data_engineering', 'data_analysis', 'research'], 'data_analysis': ['data_science', 'finance'], 'finance': ['accounting', 'fundraising'], 'accounting': ['finance'], 'policy': ['communications', 'research', 'legal'], 'research': ['policy', 'data_science'], 'communications': ['fundraising', 'sales', 'policy'], 'fundraising': ['sales', 'communications'], 'legal': ['policy'], 'operations': ['program_management', 'hr'], 'program_management': ['operations'], 'clinical': ['education', 'research'], 'education': ['clinical'], 'hr': ['operations'], 'support': ['sales', 'software'], 'sales': ['fundraising', 'support']}


def clean(value):
    text = re.sub(r'<[^>]+>', ' ', str(value or ''))
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[email]', text)
    text = re.sub(r'https?://\S+', '[link]', text)
    return ' '.join(text.split())


def job_text(row):
    return f"Title: {clean(row['title'])[:300]}\nRequirements: {clean(row.get('requirements'))[:900]}\nDescription: {clean(row.get('description'))[:2400]}"


def split_for(group):
    value = int(hashlib.sha256(str(group).encode()).hexdigest()[:8], 16) % 100
    return 'train' if value < 70 else ('dev' if value < 85 else 'test')


def build(rows, seed=20261013):
    rng = random.Random(seed)
    # A union keeps duplicates across organizations out of different splits too.
    parent = {}
    def root(key):
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = root(parent[key])
        return parent[key]
    def union(left, right):
        left, right = root(left), root(right)
        parent[max(left, right)] = min(left, right)
    seen = {}
    for row in rows:
        fingerprint = hashlib.sha256((clean(row['title']).lower() + clean(row.get('description')).lower()).encode()).hexdigest()
        org = str(row['organization_id'])
        if fingerprint in seen:
            union(org, seen[fingerprint])
        seen[fingerprint] = org
    pools = {split: defaultdict(list) for split in ['train', 'dev', 'test']}
    dedup = set()
    for row in rows:
        families = [key for key, pattern in PATTERNS.items() if pattern.search(row['title'])]
        if len(families) != 1:
            continue
        text = job_text(row)
        fingerprint = hashlib.sha256(text.lower().encode()).hexdigest()
        if fingerprint in dedup:
            continue
        dedup.add(fingerprint)
        split = split_for(root(str(row['organization_id'])))
        pools[split][families[0]].append((row, text))
    output = {split: [] for split in pools}
    for split, families in pools.items():
        for family, listings in sorted(families.items()):
            for row, text in listings:
                # Different query paraphrases share the same organization split.
                for phrase in FAMILIES[family][1]:
                    query = rng.choice(['I am looking for remote {} roles.', 'Find jobs focused on {}.', 'My target work is {}.']).format(phrase)
                    query_id = hashlib.sha256((str(row['id']) + query).encode()).hexdigest()[:16]
                    base = {'query': query, 'query_id': query_id, 'family': family, 'label_source': 'title_family_weak_v1'}
                    output[split].append({**base, 'text': text, 'label': 1, 'job_id': row['id'], 'organization_group': root(str(row['organization_id']))})
                    near = [key for key in NEAR.get(family, []) if families.get(key)]
                    other = [key for key in families if key != family]
                    if not other:
                        continue
                    negative_families = [rng.choice(near or other), rng.choice(other)]
                    for negative_family in dict.fromkeys(negative_families):
                        negative, negative_text = rng.choice(families[negative_family])
                        output[split].append({**base, 'text': negative_text, 'label': 0, 'job_id': negative['id'], 'organization_group': root(str(negative['organization_id']))})
        rng.shuffle(output[split])
    return output


def write_dataset(rows, destination, seed=20261013):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    splits = build(rows, seed)
    for split, data in splits.items():
        (destination / f'{split}.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in data))
    manifest = {'seed': seed, 'source_rows': len(rows), 'label_source': 'title_family_weak_v1', 'limitations': ['Weak occupational labels, not human judgments', 'Synthetic paraphrases; no private briefs or CVs', 'No salary, geography or eligibility labels', 'Organizations and duplicate content do not cross splits'], 'splits': {split: {'pairs': len(data), 'positive': sum(row['label'] for row in data), 'families': dict(Counter(row['family'] for row in data))} for split, data in splits.items()}}
    (destination / 'dataset.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text().splitlines() if line.strip()]
    print(json.dumps(write_dataset(rows, args.output), indent=2))
