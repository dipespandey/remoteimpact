"""Summarize bounded Scout replays without treating model verdicts as ground truth.

Usage: python -m ml.scout_reranker.live_evaluation report.jsonl [report2.jsonl]
Pass --labels labels.json for optional source-reviewed query/job judgments.
Labels: [{"query_id":"h01","job_id":123,"relevant":true,"note":"..."}].
"""
import argparse
import json
import math
import statistics
from pathlib import Path


def load_rows(paths):
    rows=[]
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if line.startswith('{'):
                row=json.loads(line)
                if 'seconds' in row and 'id' in row:rows.append(row)
    return rows


def summarize(rows, labels=()):
    judgments={(x['query_id'],x['job_id']):x for x in labels}
    result={}
    for run in sorted({r.get('run',0) for r in rows}):
        group=[r for r in rows if r.get('run',0)==run]
        times=sorted(r['seconds'] for r in group)
        pairs=[(r['id'],j['id']) for r in group for j in r.get('matches',[])]
        reviewed=[judgments[p] for p in pairs if p in judgments]
        result['cold' if run==0 else f'warm_{run}']={
            'queries':len(group),'median_seconds':round(statistics.median(times),3),
            'p90_seconds':times[math.ceil(.9*len(times))-1],'max_seconds':max(times),
            'returned_pairs':len(pairs),'zero_result_queries':sum(not r.get('matches') for r in group),
            'errors':sum('error' in r for r in group),
            'review_unavailable':sum(r.get('diagnostics',{}).get('reranker')=='review_unavailable' for r in group),
            'review_incomplete':sum(bool(r.get('diagnostics',{}).get('review_incomplete')) for r in group),
            'source_reviewed_pairs':len(reviewed),
            'source_reviewed_relevant':sum(x['relevant'] is True for x in reviewed),
            'source_reviewed_irrelevant':sum(x['relevant'] is False for x in reviewed),
        }
    repeated=[]
    for query in sorted({r['id'] for r in rows}):
        runs=[r for r in rows if r['id']==query]
        if len(runs)<2:continue
        a={j['id'] for j in runs[0].get('matches',[])}
        b={j['id'] for j in runs[1].get('matches',[])}
        repeated.append({'id':query,'same_results':a==b,'jaccard':len(a&b)/len(a|b) if a|b else None})
    result['repeat_stability']=repeated
    result['limitations']='Source review is optional and not independent human ground truth. Empty results do not establish missing inventory. Recall is not measured.'
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports',nargs='+')
    parser.add_argument('--labels')
    args=parser.parse_args()
    print(json.dumps(summarize(load_rows(args.reports),json.loads(Path(args.labels).read_text()) if args.labels else []),indent=2))
