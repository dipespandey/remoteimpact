"""Generate an offline blind reviewer worksheet. No API calls or external assets."""
import argparse
import hashlib
import json
from pathlib import Path

from .better_data import read_jsonl


def render(rows):
    blind=[]
    for row in rows:
        identifier=row.get('pair_id') or hashlib.sha256((row['query_id']+':'+str(row['job_id'])).encode()).hexdigest()[:24]
        blind.append({'pair_id':identifier,'query':row['query'],'text':row['text']})
    serialized=json.dumps(blind,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    return HTML.replace('__PAIRS__',serialized)


HTML='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Remote Impact · Blind relevance review</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#fafaf8;color:#202024;font:16px/1.6 system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:32px 20px}h1{font-size:28px;letter-spacing:-.03em;margin:0}
header p{color:#62626c;max-width:760px}section{background:white;border:1px solid #dedee4;border-radius:16px;padding:24px;margin:20px 0}
h2{font-size:14px;color:#62626c;text-transform:uppercase;letter-spacing:.08em;margin-top:0}
pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}button{font:inherit;border:1px solid #dedee4;border-radius:10px;padding:10px 16px;background:white;cursor:pointer}
button:hover,button[aria-pressed=true]{background:#e7f3ed;border-color:#488464}button:focus-visible,textarea:focus-visible{outline:3px solid #488464;outline-offset:3px}
button:disabled{opacity:.45;cursor:default}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}textarea{width:100%;font:inherit;padding:12px;border:1px solid #dedee4;border-radius:10px;margin:12px 0}label{font-size:14px}
.toolbar{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:20px 0}.small{font-size:14px;color:#62626c}
</style>
<main><header><h1>Review job relevance</h1><p>Read the synthetic brief and public listing. Judge professional responsibilities, skills, stated seniority and explicit role exclusions. Salary and geography are handled separately. Choose “Uncertain” when evidence is insufficient.</p>
<p class="small">Teacher labels and model scores are hidden. Decisions save in this browser; export them when finished.</p></header>
<div class="toolbar"><span id="progress" role="status"></span><button id="export">Export reviewed labels</button></div>
<section><h2>Search brief</h2><pre id="query"></pre></section>
<section><h2>Public job listing</h2><pre id="job"></pre></section>
<section><h2>Is this role professionally relevant?</h2><div class="row" role="group" aria-label="Relevance judgment">
<button data-label="1" aria-pressed="false">Relevant · 1</button><button data-label="0" aria-pressed="false">Irrelevant · 2</button><button data-label="uncertain" aria-pressed="false">Uncertain · 3</button></div>
<p><label for="notes">Evidence or reviewer notes</label><textarea id="notes" rows="3"></textarea></p>
<div class="toolbar"><button id="previous">Previous</button><span id="position"></span><button id="next">Next</button></div></section></main>
<script>
const pairs=__PAIRS__;
const key='remoteimpact-blind-review-v2:'+pairs.map(row=>row.pair_id).join(',');
let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(error){}
let position=0;
const get=id=>document.getElementById(id);
function persist(){try{localStorage.setItem(key,JSON.stringify(saved))}catch(error){} }
function update(){
 const pair=pairs[position],decision=saved[pair.pair_id]||{};
 get('query').textContent=pair.query;get('job').textContent=pair.text;
 get('notes').value=decision.reviewer_notes||'';
 document.querySelectorAll('[data-label]').forEach(button=>button.setAttribute('aria-pressed',String(String(decision.reviewed_label)===button.dataset.label)));
 get('position').textContent=(position+1)+' / '+pairs.length;
 get('progress').textContent=Object.values(saved).filter(row=>row.reviewed_label!==undefined).length+' of '+pairs.length+' reviewed';
 get('previous').disabled=position===0;get('next').disabled=position===pairs.length-1;
}
function choose(label){const pair=pairs[position];saved[pair.pair_id]={...(saved[pair.pair_id]||{}),pair_id:pair.pair_id,reviewed_label:label==='uncertain'?label:Number(label),reviewer_notes:get('notes').value,reviewed_at:new Date().toISOString()};persist();update()}
document.querySelectorAll('[data-label]').forEach(button=>button.addEventListener('click',()=>choose(button.dataset.label)));
get('notes').addEventListener('input',()=>{const pair=pairs[position];saved[pair.pair_id]={...(saved[pair.pair_id]||{}),pair_id:pair.pair_id,reviewer_notes:get('notes').value};persist()});
get('previous').addEventListener('click',()=>{position--;update()});get('next').addEventListener('click',()=>{position++;update()});
document.addEventListener('keydown',event=>{if(event.target.tagName==='TEXTAREA')return;const labels={'1':'1','2':'0','3':'uncertain'};if(labels[event.key])choose(labels[event.key])});
get('export').addEventListener('click',()=>{const rows=pairs.filter(pair=>saved[pair.pair_id]?.reviewed_label!==undefined).map(pair=>({...pair,...saved[pair.pair_id],label_source:'human_review_v2'}));
const blob=new Blob([rows.map(row=>JSON.stringify(row)).join('\\n')+'\\n'],{type:'application/x-ndjson'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='remoteimpact-reviewed-relevance.jsonl';a.click();URL.revokeObjectURL(url)});
if(pairs.length)update();else get('progress').textContent='No pairs available';
</script></html>'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',default='ml/data/scout-v2/review.jsonl')
    parser.add_argument('--output',default='ml/artifacts/scout-v2-expanded/review.html')
    args=parser.parse_args()
    Path(args.output).write_text(render(read_jsonl(args.input)))
    print('Blind review worksheet saved')


if __name__=='__main__':main()
