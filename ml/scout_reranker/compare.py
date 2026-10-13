"""Fixed-candidate comparison against v1 and the current provider decision layer."""
import argparse
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from .better_data import cache_result, read_jsonl, write_jsonl
from .data import clean


def production_system():
    source=Path('jobs/services/scout_reranker.py').read_text()
    tree=ast.parse(source)
    function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='rerank')
    assignment=next(node for node in function.body if isinstance(node,ast.Assign)
                    and any(isinstance(target,ast.Name) and target.id=='system' for target in node.targets))
    return ast.literal_eval(assignment.value)


def production_validator():
    # Compile only the two pure functions from trusted repository source. This
    # avoids importing Django's eager services package in the offline experiment.
    tree=ast.parse(Path('jobs/services/scout_reranker.py').read_text())
    functions=[node for node in tree.body if isinstance(node,ast.FunctionDef)
               and node.name in {'_normalized','validate_decisions'}]
    if len(functions)!=2:raise ValueError('Production decision validation changed')
    namespace={'json':json,'FIELDS':('title','description','requirements')}
    exec(compile(ast.Module(body=functions,type_ignores=[]),'production_validation','exec'),namespace)
    return namespace['validate_decisions']


def current_tasks(label_tasks,corpus):
    by_id={row['id']:row for row in corpus}
    system=production_system()
    output=[]
    for task in label_tasks:
        if task['meta']['split']!='test':continue
        candidates=[]
        for candidate in task['meta']['candidates']:
            row=by_id[candidate['id']]
            candidates.append({'id':row['id'],**{field:clean(row.get(field))[:900]
                for field in ['title','description','requirements']}})
        output.append({'model':'deepseek-flash','system':system,
            'payload':{'request':task['meta']['query'][:2000],'candidates':candidates},
            'max_tokens':1400,'meta':{'query_id':task['meta']['query_id'],
                'scope':'current_relevance_prompt_on_fixed_public_candidate_pool',
                'source_task_id':hashlib.sha256(json.dumps(task,sort_keys=True).encode()).hexdigest()}})
    return output


def provider_metrics(tasks,rows,cache):
    validate_decisions=production_validator()
    by_query={task['meta']['query_id']:task for task in tasks}
    selected={};missing=invalid=0
    for query_id,task in by_query.items():
        result=cache_result(task,cache)
        if result is None:missing+=1;continue
        try:
            evidence={row['id']:row for row in task['payload']['candidates']}
            decisions=validate_decisions(json.dumps(result['output']),evidence)
            selected[query_id]=[identifier for identifier,quotes in decisions]
        except ValueError:invalid+=1
    scored=[row for row in rows if row['query_id'] in selected]
    tp=fp=fn=tn=0
    for row in scored:
        decision=row['job_id'] in selected[row['query_id']]
        if decision and row['label']:tp+=1
        elif decision:fp+=1
        elif row['label']:fn+=1
        else:tn+=1
    return {'pairs':len(scored),'groups':len(selected),'precision':tp/max(1,tp+fp),
        'recall':tp/max(1,tp+fn),'true_positives':tp,'false_positives':fp,'false_negatives':fn,
        'true_negatives':tn,'missing_provider_groups':missing,'invalid_provider_groups':invalid,
        '_covered_query_ids':sorted(selected),
        'scope':'Provider relevance decisions only, not the full Scout pipeline',
        'oracle':'Pro weak labels on student-visible evidence, not independently human-reviewed truth'}


def breakdown(rows,scores,threshold):
    from .experiment import metrics
    output={}
    for field in ['family','kind','label_source']:
        groups=defaultdict(list)
        for row,score in zip(rows,scores):groups[row.get(field,'unknown')].append((row,score))
        output[field]={}
        for key,values in groups.items():
            subset=[row for row,score in values];values_scores=[score for row,score in values]
            if len({row['label'] for row in subset})==2:
                output[field][key]=metrics(subset,values_scores,threshold)
            else:
                selected=[row for row,score in values if score>=threshold]
                output[field][key]={'pairs':len(subset),'positive':sum(row['label'] for row in subset),
                    'selected':len(selected),'false_positives':sum(not row['label'] for row in selected),
                    'false_negatives':sum(row['label'] and score<threshold for row,score in values)}
    return output


def evaluate(args):
    import torch
    from transformers import AutoTokenizer,AutoModelForSequenceClassification
    from .experiment import predict,metrics,calibrate
    torch.set_num_threads(4)
    root=Path(args.data)
    dev,test=[read_jsonl(root/f'{split}.jsonl') for split in ['dev','test']]
    golden=read_jsonl(Path(__file__).with_name('acceptance.jsonl'))
    confirmation=read_jsonl(root/'confirmation.jsonl') if (root/'confirmation.jsonl').exists() else []
    report={'data':json.loads((root/'dataset.json').read_text()),'models':{},
        'acceptance_sha256':hashlib.sha256(Path(__file__).with_name('acceptance.jsonl').read_bytes()).hexdigest(),
        'production_ready':False}
    all_scores={}
    candidates=[('v1',args.v1),('v2',args.v2)]
    if args.strict_v2:candidates.append(('v2_strict',args.strict_v2))
    for key,directory in candidates:
        directory=Path(directory)
        card=json.loads((directory/'remoteimpact_model.json').read_text())
        digest=hashlib.sha256((directory/'model.safetensors').read_bytes()).hexdigest()
        if digest!=card['weights_sha256']:raise ValueError('Comparison checkpoint checksum mismatch')
        tokenizer=AutoTokenizer.from_pretrained(directory,local_files_only=True)
        model=AutoModelForSequenceClassification.from_pretrained(directory,local_files_only=True).to(args.device).eval()
        scores=predict(model,tokenizer,test,args.device)
        all_scores[key]=scores
        gold_scores=predict(model,tokenizer,golden,args.device)
        confirmation_scores=predict(model,tokenizer,confirmation,args.device) if confirmation else []
        threshold=card['calibration']['threshold']
        dev_scores=predict(model,tokenizer,dev,args.device)
        recalibrated=calibrate(dev,dev_scores)
        report['models'][key]={'parameters':card['parameters'],'weights_sha256':digest,
            'frozen_threshold':threshold,'test':metrics(test,scores,threshold),
            'golden':metrics(golden,gold_scores,threshold),'per_domain':breakdown(test,scores,threshold),
            'dev_recalibrated':recalibrated,
            'test_at_dev_recalibrated_threshold':metrics(test,scores,recalibrated['threshold'])}
        report['models'][key]['development_operating_points']={}
        for target in [0.90,0.95,0.97]:
            calibration=calibrate(dev,dev_scores,min_precision=target)
            report['models'][key]['development_operating_points'][str(target)]={
                'calibration':calibration,'test':metrics(test,scores,calibration['threshold']),
                'golden':metrics(golden,gold_scores,calibration['threshold'])}
            if confirmation:
                report['models'][key]['development_operating_points'][str(target)]['confirmation']=metrics(
                    confirmation,confirmation_scores,calibration['threshold'])
        if confirmation:
            report['models'][key]['confirmation']=metrics(confirmation,confirmation_scores,threshold)
        write_jsonl(root/f'{key}-test-predictions.jsonl',[{**row,'score':score,'selected':score>=threshold}
                                                       for row,score in zip(test,scores)])
        del model
        if args.device=='mps':torch.mps.empty_cache()
    report['current_deepseek']=provider_metrics(read_jsonl(Path(args.work)/'current-tasks.jsonl'),test,args.cache)
    covered=set(report['current_deepseek'].pop('_covered_query_ids'))
    common_rows=[row for row in test if row['query_id'] in covered]
    for key,scores in all_scores.items():
        common_scores=[score for row,score in zip(test,scores) if row['query_id'] in covered]
        model_report=report['models'][key]
        model_report['common_provider_subset']=metrics(common_rows,common_scores,model_report['frozen_threshold']) if common_rows else None
        if common_rows:
            for point in model_report['development_operating_points'].values():
                point['common_provider_subset']=metrics(common_rows,common_scores,point['calibration']['threshold'])
    if confirmation:report['confirmation_set']=json.loads((root/'confirmation.json').read_text())
    Path(args.report).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'models':{key:{'test':value['test'],'golden':value['golden']}
        for key,value in report['models'].items()},'current_deepseek':report['current_deepseek']},indent=2))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['prepare-current','evaluate'])
    parser.add_argument('--work',default='ml/data/scout-v2-work')
    parser.add_argument('--corpus',default='.audit/public-jobs-training-export.jsonl')
    parser.add_argument('--data',default='ml/data/scout-v2')
    parser.add_argument('--cache',default='ml/data/scout-v2-work/cache')
    parser.add_argument('--v1',default='ml/artifacts/scout-v1/minilm')
    parser.add_argument('--v2',default='ml/artifacts/scout-v2/minilm')
    parser.add_argument('--strict-v2')
    parser.add_argument('--report',default='ml/scout_reranker/evaluation-v2-2026-10-13.json')
    parser.add_argument('--device',default='mps')
    args=parser.parse_args()
    if args.stage=='prepare-current':
        tasks=current_tasks(read_jsonl(Path(args.work)/'label-tasks.jsonl'),read_jsonl(args.corpus))
        write_jsonl(Path(args.work)/'current-tasks.jsonl',tasks)
        print(json.dumps({'event':'current_comparison_prepared','tasks':len(tasks)}))
    else:evaluate(args)


if __name__=='__main__':main()
