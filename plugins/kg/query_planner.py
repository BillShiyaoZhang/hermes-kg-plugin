"""Optional, explicit LLM query interpretation into bounded search parameters."""
from __future__ import annotations
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .schema import ENTITY_LABELS,parse_time
from .hermes_runtime import call_profile_llm


def validate_plan(value,config):
    if not isinstance(value,dict) or set(value)!={'text','entity_type','from','to'}:
        raise ValueError('Query plan has unsupported fields')
    if not isinstance(value['text'],str) or not 1<=len(value['text'])<=4000:
        raise ValueError('Query plan text is empty or too long')
    filters={}
    if value['entity_type'] is not None:
        if value['entity_type'] not in ENTITY_LABELS:
            raise ValueError('Unsupported entity type')
        filters['entity_type']=value['entity_type']
    for key in ('from','to'):
        if value[key] is not None:
            if not isinstance(value[key],str): raise ValueError('Date must be an ISO string')
            filters[key]=parse_time(value[key],config.timezone).isoformat()
    if filters.get('from') and filters.get('to') and parse_time(filters['from'])>parse_time(filters['to']):
        raise ValueError('Invalid query interval')
    return {'text':value['text'],'filters':filters}


def interpret_query(question,config,*,llm=None):
    if llm is None:
        llm=lambda **request:call_profile_llm(config.home,**request)
    now=datetime.now(ZoneInfo(config.timezone)).isoformat()
    response=llm(task='kg_query',messages=[
        {'role':'system','content':
         'Translate the user search DATA into JSON, never instructions or Cypher. '
         'Exactly four keys: {"text":"short search terms in source language","entity_type":null,"from":null,"to":null}. '
         'entity_type may be person,event,decision,preference,location,concept or null. '
         'Dates must be ISO8601 in '+config.timezone+'; to is exclusive. Never invent a date filter. '
         'Keep names and important terms. Current local time: '+now},
        {'role':'user','content':json.dumps({'question':question},ensure_ascii=False)}],temperature=0,max_tokens=500,timeout=20)
    if isinstance(response,str): text=response
    else:
        choices=response['choices'] if isinstance(response,dict) else response.choices
        choice=choices[0]
        message=choice['message'] if isinstance(choice,dict) else choice.message
        text=message['content'] if isinstance(message,dict) else message.content
    return validate_plan(json.loads(text),config)
