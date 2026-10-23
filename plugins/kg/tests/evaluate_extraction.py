#!/usr/bin/env python3
"""Opt-in extraction quality evaluation through a real configured Hermes route.

Only the checked-in synthetic fixture is sent. No graph is opened or written.
Prepare a separate profile with the intended model route and credentials, then
pass --home and --output. Output includes synthetic model responses and should
normally live in the ignored reports/ directory. No secrets are reported.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from kg.config import load_config
from kg.hermes_runtime import prepare_runtime
from kg.runner import _extract_validated
from kg.schema import parse_time

FIXTURE=Path(__file__).parent/'fixtures'/'extraction_quality.json'


def _matches(entity,expected,config):
    kind=str(entity.get('type',entity.get('entity_type',''))).casefold()
    if kind!=expected['type'].casefold():
        return False
    text=json.dumps(entity,ensure_ascii=False).casefold()
    if not all(any(term.casefold() in text for term in group) for group in expected.get('terms',[])):
        return False
    if any(term.casefold() in text for term in expected.get('forbidden_terms',[])):
        return False
    if expected.get('event_time'):
        props=entity.get('props',entity.get('properties',{}))
        value=props.get('event_time',props.get('hermes_eventTime'))
        if value is None or parse_time(value,config.timezone)!=parse_time(expected['event_time'],config.timezone):
            return False
    return True


def evaluate_case(case,config):
    start=time.monotonic()
    timestamp=datetime.fromisoformat(case.get('timestamp','2026-10-23T12:00:00+08:00')).timestamp()
    messages=[{'id':index,'timestamp':timestamp+index,**message} for index,message in enumerate(case['messages'],1)]
    snapshot={'session_id':'synthetic-'+case['id'],'messages':messages}
    # Match SessionSource's opaque SHA256 identifier format; human-readable
    # fixture IDs can induce normalization errors that real snapshots lack.
    snapshot['snapshot_id']=hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    raw=[]
    calls=[]
    report={'id':case['id'],'category':case['category'],'expected_count':len(case['expected'])}
    try:
        payload=_extract_validated(snapshot,config,on_call=lambda:calls.append(1),on_response=lambda text,attempt:raw.append({'attempt':attempt,'text':text}))
        unmatched=list(case['expected'])
        true_positive=0
        supported_context=0
        for entity in payload['entities']:
            match=next((expected for expected in unmatched if _matches(entity,expected,config)),None)
            if match is not None:
                true_positive+=1
                unmatched.remove(match)
            elif any(_matches(entity,expected,config) for expected in case.get('allowed_context',[])):
                supported_context+=1
        report.update(valid=True,entities=payload['entities'],relations=payload['relations'],
                      true_positive=true_positive,supported_context_entities=supported_context,
                      false_positive=len(payload['entities'])-true_positive-supported_context,
                      false_negative=len(unmatched))
    except Exception as exc:
        # Error messages can contain SDK response bodies. Persist only the safe
        # exception class; raw output below contains synthetic fixture data.
        report.update(valid=False,error_type=type(exc).__name__,true_positive=0,false_positive=0,false_negative=len(case['expected']))
    report.update(llm_calls=len(calls),correction_attempts=max(0,len(calls)-1),
                  milliseconds=round((time.monotonic()-start)*1000,1),responses=raw)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--workers',type=int,default=2,choices=range(1,5))
    parser.add_argument('--cases',nargs='*',help='Optional exact fixture case IDs')
    args=parser.parse_args(argv)
    fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    if not fixture.get('synthetic'):
        raise ValueError('Only an explicitly synthetic fixture is supported')
    cases=[case for case in fixture['cases'] if not args.cases or case['id'] in args.cases]
    if not cases:
        raise ValueError('No fixture cases selected')
    config=load_config(home=args.home)
    prepare_runtime(config.home)
    # Current Hermes imports its entrypoint while creating HTTP clients. Avoid
    # installation repair/update during this opt-in evaluation; the real
    # dotenv loader, provider resolution, SDK and external request remain active.
    sys.argv=['kg-extraction-evaluation','pm','repair']
    from agent.auxiliary_client import get_text_auxiliary_client,shutdown_cached_clients
    _,resolved_model=get_text_auxiliary_client(task='kg_sync')
    rows=[]
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as workers:
            futures={workers.submit(evaluate_case,case,config):case['id'] for case in cases}
            for future in as_completed(futures):
                row=future.result();rows.append(row)
                print(json.dumps({key:row[key] for key in ('id','valid','true_positive','false_positive','false_negative','llm_calls','milliseconds')},ensure_ascii=False),flush=True)
    finally:
        shutdown_cached_clients()
    tp=sum(row['true_positive'] for row in rows);fp=sum(row['false_positive'] for row in rows);fn=sum(row['false_negative'] for row in rows)
    report={'synthetic':True,'personal_transcripts_sent':0,'graph_writes':0,'real_hermes_route':True,'resolved_model':resolved_model,
            'fixture':FIXTURE.name,'cases':len(rows),'valid_cases':sum(row['valid'] for row in rows),
            'llm_calls':sum(row['llm_calls'] for row in rows),'correction_attempts':sum(row['correction_attempts'] for row in rows),
            'supported_context_entities':sum(row.get('supported_context_entities',0) for row in rows),
            'fixture_fact_precision':tp/(tp+fp) if tp+fp else 1,'fixture_fact_recall':tp/(tp+fn) if tp+fn else 1,
            'true_positive':tp,'false_positive':fp,'false_negative':fn,
            'measurement':'Type, required content and event-time matches against required synthetic facts; explicitly allowed contextual concepts do not count as required facts or false positives. Not a general semantic benchmark.',
            'results':sorted(rows,key=lambda row:row['id'])}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='results'},ensure_ascii=False),flush=True)
    return 0 if report['valid_cases']==len(rows) and not fp and not fn else 1


if __name__=='__main__':
    raise SystemExit(main())
