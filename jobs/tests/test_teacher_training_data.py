import json
import re
from pathlib import Path
import tempfile
from types import SimpleNamespace

from django.test import SimpleTestCase

from ml.scout_reranker.better_data import (accepted_briefs, accepted_decisions, generate_tasks,
                                          partitions, finalize, GEN_SYSTEM, LABEL_SYSTEM)
from ml.scout_reranker.teacher import Budget, BudgetExceeded, task_id
from ml.scout_reranker.compare import production_validator, current_tasks
from ml.scout_reranker.review import render as render_review


class BetterDataTests(SimpleTestCase):
    def corpus(self):
        rows=[]
        for org in range(1,90):
            for number,title in enumerate(['Backend Software Engineer','Data Scientist','Policy Analyst']):
                rows.append({'id':org*10+number,'organization_id':org,'title':title,
                    'description':f'{title} public reference {org}. ' + 'Build and evaluate programme services. '*8,
                    'requirements':'Professional relevant experience.'})
        rows.append({**rows[0],'organization_id':999,'id':9999})
        return rows

    def test_partition_and_anchor_generation_are_disjoint_and_reproducible(self):
        corpus=partitions(self.corpus())
        seen={}
        for split,rows in corpus.items():
            for row in rows:self.assertEqual(seen.setdefault(row['organization_group'],split),split)
        tasks=generate_tasks(corpus)
        self.assertEqual(tasks,generate_tasks(corpus))
        self.assertEqual(len(tasks),len({task_id(task) for task in tasks}))
        self.assertEqual({task['meta']['split'] for task in tasks},{'train','dev','test'})
        self.assertTrue(all(task['system']==GEN_SYSTEM for task in tasks))

    def test_ungrounded_or_contact_containing_briefs_are_rejected(self):
        task={'payload':{'listing':'Build Python software services and evaluate systems.'},
              'meta':{'split':'train','anchor_id':1,'family':'software'}}
        query='I want to build Python software services, work on reliable APIs, and avoid recruitment.'
        briefs=[{'kind':kind,'query':query,'quote':'Build Python software services'}
                for kind in ['responsibilities','exclusion']]
        self.assertEqual(len(accepted_briefs(task,{'output':{'briefs':briefs}})),2)
        for edit in [{'quote':'Invented supporting evidence'},{'query':query+' https://example.org'},
                     {'query':query+' contact person@example.org'}]:
            with self.assertRaises(ValueError):
                accepted_briefs(task,{'output':{'briefs':[{**briefs[0],**edit},briefs[1]]}})

    def label_task(self):
        return {'system':LABEL_SYSTEM,'payload':{'brief':'Software work',
            'candidates':[{'id':1,'text':'build python software services'},
                          {'id':2,'text':'recruit software engineers'},
                          {'id':3,'text':'unclear programme role'}]},
            'meta':{'query':'Software work','query_id':'fixed','anchor_id':1,'family':'software',
                'kind':'exclusion','candidates':[{'id':pk,'text':text,'organization_group':str(pk)}
                    for pk,text in [(1,'Build Python software services'),(2,'Recruit software engineers'),(3,'Unclear programme role')]]}}

    def test_uncertainty_is_not_converted_to_a_negative(self):
        result={'served_model':'teacher','output':{'decisions':[
            {'id':1,'judgment':'relevant','quote':'build python software','reason':'Core function matches'},
            {'id':2,'judgment':'irrelevant','quote':'recruit software engineers','reason':'Recruiting, not building'},
            {'id':3,'judgment':'uncertain','quote':'','reason':'Insufficient evidence'}]}}
        rows,uncertain=accepted_decisions(self.label_task(),result)
        self.assertEqual([(row['job_id'],row['label']) for row in rows],[(1,1),(2,0)])
        self.assertEqual(uncertain,1)
        self.assertTrue(all(not row['human_reviewed'] for row in rows))
        result['output']['decisions'][1]['quote']='invented evidence'
        rows,uncertain=accepted_decisions(self.label_task(),result)
        self.assertEqual(len(rows),1)
        self.assertEqual(uncertain,2)

    def test_duplicate_or_injected_job_ids_fail_closed(self):
        result={'served_model':'teacher','output':{'decisions':[
            {'id':pk,'judgment':'uncertain','quote':'','reason':'Unclear'} for pk in [1,2,2]]}}
        with self.assertRaisesMessage(ValueError,'Unknown or repeated'):
            accepted_decisions(self.label_task(),result)
        result['output']['decisions'][-1]['id']=999
        with self.assertRaisesMessage(ValueError,'Unknown or repeated'):
            accepted_decisions(self.label_task(),result)

    def test_provider_comparison_reuses_current_quote_validation(self):
        from jobs.services.scout_reranker import validate_decisions
        evidence={1:{'description':'Build Python software services'}}
        output=json.dumps({'decisions':[{'id':1,'relevant':True,
            'evidence':[{'field':'description','quote':'Build Python software'}]}]})
        self.assertEqual(production_validator()(output,evidence),validate_decisions(output,evidence))
        forged=json.dumps({'decisions':[{'id':1,'relevant':True,
            'evidence':[{'field':'description','quote':'Forged supporting evidence'}]}]})
        with self.assertRaises(ValueError):production_validator()(forged,evidence)

    def test_provider_comparison_only_uses_held_out_queries(self):
        task=self.label_task()
        task['meta']['split']='train'
        corpus=[{'id':pk,'title':'Public Role','description':'Description','requirements':''} for pk in [1,2,3]]
        self.assertEqual(current_tasks([task],corpus),[])
        task['meta']['split']='test'
        output=current_tasks([task],corpus)
        self.assertEqual(len(output),1)
        self.assertEqual(output[0]['meta']['query_id'],'fixed')

    def test_replay_cannot_leak_an_employer_into_the_test_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cache=root/'cache';cache.mkdir()
            replay=root/'replay';replay.mkdir();destination=root/'output'
            task=self.label_task();task['meta']['split']='test'
            result={'task_id':task_id(task),'served_model':'teacher','output':{'decisions':[
                {'id':1,'judgment':'relevant','quote':'build python software','reason':'Main function'},
                {'id':2,'judgment':'uncertain','quote':'','reason':'Unclear'},
                {'id':3,'judgment':'uncertain','quote':'','reason':'Unclear'}]}}
            (cache/f'{task_id(task)}.json').write_text(json.dumps(result))
            # Different text/query still leaks if the employer occurs in both.
            (replay/'train.jsonl').write_text(json.dumps({'family':'software','label':1,
                'organization_group':'1','text':'Another role','query_id':'legacy',
                'label_source':'title_family_weak_v1'})+'\n')
            with self.assertRaisesMessage(ValueError,'partition leakage'):
                finalize([task],cache,destination,replay)
            self.assertFalse((destination/'train.jsonl').exists())
            self.assertFalse((destination/'dataset.json').exists())

    def test_blind_review_hides_teacher_labels_and_escapes_public_content(self):
        query='</script><script>alert("listing injection")</script>'
        html=render_review([{'query':query,'text':'Public listing','query_id':'fixed','job_id':1,
            'teacher_reason':'secret teacher judgment','label':1,'score':0.987654321}])
        self.assertNotIn(query,html)
        self.assertNotIn('secret teacher judgment',html)
        self.assertNotIn('0.987654321',html)
        payload=re.search(r'const pairs=(.*?);\nconst key=',html,re.S).group(1)
        decoded=json.loads(payload)
        self.assertEqual(decoded[0]['query'],query)
        self.assertEqual(set(decoded[0]),{'pair_id','query','text'})


class TeacherBudgetTests(SimpleTestCase):
    def test_budget_reserves_before_calls_and_charges_interrupted_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'usage.jsonl'
            task={'model':'deepseek-v4-pro','system':'Rules','payload':{'text':'Job'}}
            budget=Budget(path,0.0001)
            with self.assertRaises(BudgetExceeded):budget.reserve(task,1000)
            self.assertEqual(budget.spent,0)
            budget=Budget(path,1)
            attempt=budget.reserve(task,1000)
            ceiling=budget.spent
            self.assertGreater(ceiling,0)
            self.assertEqual(Budget(path,1).spent,ceiling)
            budget.finish(attempt,task,SimpleNamespace(prompt_tokens=50,completion_tokens=100))
            self.assertLess(budget.spent,ceiling)
            self.assertEqual(Budget(path,1).spent,budget.spent)
            events=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([event['state'] for event in events],['reserved','finished'])
