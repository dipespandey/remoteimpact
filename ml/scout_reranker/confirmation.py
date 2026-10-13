"""Fresh confirmation data: employers unseen in training, dev, or prior tests."""
import argparse
from pathlib import Path
import json

from .better_data import (read_jsonl,write_jsonl,generate_tasks,accepted_briefs,cache_result,
                         label_tasks,accepted_decisions,GEN_SYSTEM)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['prepare','mine','finalize'])
    parser.add_argument('--work',default='ml/data/scout-v2-work')
    parser.add_argument('--cache',default='ml/data/scout-v2-work/cache')
    parser.add_argument('--data',default='ml/data/scout-v2')
    args=parser.parse_args();work=Path(args.work)
    if args.stage=='prepare':
        used={row['organization_group'] for task in read_jsonl(work/'label-tasks.jsonl')
              for row in task['meta']['candidates']}
        corpus=json.loads((work/'corpus.json').read_text())
        remaining=[row for row in corpus['test'] if row['organization_group'] not in used]
        corpus={'train':[],'dev':[],'test':remaining}
        (work/'confirmation-corpus.json').write_text(json.dumps(corpus,ensure_ascii=False))
        tasks=generate_tasks(corpus,seed=881726)
        # Novel functions often have titles outside the bootstrap taxonomy.
        # Reserve more of these for the final check rather than omitting them.
        anchored={task['meta']['anchor_id'] for task in tasks}
        covered={row['organization_group'] for row in remaining if row['id'] in anchored}
        other_count=sum(task['meta']['family']=='other' for task in tasks)
        for row in sorted(remaining,key=lambda row:str(row['id'])):
            if other_count>=8:break
            if row['family']!='other' or row['id'] in anchored or row['organization_group'] in covered:continue
            tasks.append({'model':'deepseek-flash','system':GEN_SYSTEM,'payload':{'listing':row['text']},
                'max_tokens':750,'meta':{'split':'test','anchor_id':row['id'],'family':'other'}})
            covered.add(row['organization_group']);other_count+=1
        for task in tasks:task['meta']['confirmation']=True
        write_jsonl(work/'confirmation-generate-tasks.jsonl',tasks)
        print(json.dumps({'remaining_jobs':len(remaining),'employers':len({row['organization_group'] for row in remaining}),
                          'tasks':len(tasks)}))
    elif args.stage=='mine':
        briefs=[];invalid=0
        for task in read_jsonl(work/'confirmation-generate-tasks.jsonl'):
            result=cache_result(task,args.cache)
            if result is None:continue
            try:briefs.extend(accepted_briefs(task,result))
            except ValueError:invalid+=1
        tasks=label_tasks(json.loads((work/'confirmation-corpus.json').read_text()),briefs,seed=881726)
        for task in tasks:task['meta']['split']='confirmation'
        write_jsonl(work/'confirmation-label-tasks.jsonl',tasks)
        print(json.dumps({'tasks':len(tasks),'invalid_generation':invalid}))
    else:
        used={row['organization_group'] for task in read_jsonl(work/'label-tasks.jsonl')
              for row in task['meta']['candidates']}
        rows=[];dropped=invalid=missing=0
        for task in read_jsonl(work/'confirmation-label-tasks.jsonl'):
            result=cache_result(task,args.cache)
            if result is None:missing+=1;continue
            try:pairs,n=accepted_decisions(task,result)
            except ValueError:invalid+=1;continue
            for row in pairs:
                if row['organization_group'] in used:raise ValueError('Confirmation employer leakage')
            rows.extend(pairs);dropped+=n
        write_jsonl(Path(args.data)/'confirmation.jsonl',rows)
        summary={'pairs':len(rows),'query_groups':len({row['query_id'] for row in rows}),
                 'positive':sum(row['label'] for row in rows),'dropped_pairs':dropped,
                 'missing_tasks':missing,'invalid_tasks':invalid,
                 'human_reviewed':False,'purpose':'One final check after freezing training choices; no tuning on these labels'}
        (Path(args.data)/'confirmation.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(summary))


if __name__=='__main__':main()
