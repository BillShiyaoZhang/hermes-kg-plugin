"""Explicit synthetic development fixture; never imports a personal profile."""
from __future__ import annotations


def seed_demo(config):
    if not config.urn_prefix.startswith('urn:hermes-demo:'):
        raise ValueError('Demo fixtures require the dedicated hermes-demo namespace')
    from .store import KGStore
    prefix=config.urn_prefix
    entities=[
        {'type':'Person','id':'lin','name':'林舟','props':{'description':'演示人物：一起讨论个人记忆产品的设计伙伴。','alias_names':['小林']}},
        {'type':'Event','id':'product-review','name':'第一次产品方向讨论','props':{'event_time':'2026-10-22T15:00:00+08:00','description':'演示事件：与林舟讨论可追溯的长期记忆，决定先服务个人 Agent 深度用户。','event_type':'meeting'}},
        {'type':'Decision','id':'local-first','name':'第一版采用自托管工作台','props':{'decision_text':'先交付用户自己掌握数据的 Web 工作台。','decision_context':'降低维护负担，并能看清和修正 Agent 的记忆。','description':'演示决策：从单 profile 开始，再验证跨 Agent 需求。'}},
        {'type':'Preference','id':'evidence','name':'回忆时展示来源','props':{'preference_key':'memory_evidence','preference_value':'回忆重要的人物与决定时，附上原话来源。'}},
        {'type':'Concept','id':'memory-control','name':'可控制的个人记忆','props':{'description':'演示概念：自动积累、找回背景、查看证据，以及纠正和停用错误的记忆。'}},
        {'type':'Location','id':'studio','name':'工作室','props':{'description':'演示地点：产品讨论发生的地方。'}},
    ]
    relations=[{'from':config.user_person_urn,'to':prefix+'person-lin','type':'KNOWS'},
               {'from':prefix+'person-lin','to':prefix+'event-product-review','type':'PARTICIPATED_IN'},
               {'from':config.user_person_urn,'to':prefix+'event-product-review','type':'PARTICIPATED_IN'},
               {'from':prefix+'event-product-review','to':prefix+'location-studio','type':'AT_LOCATION'},
               {'from':prefix+'decision-local-first','to':prefix+'concept-memory-control','type':'RELATED_TO'}]
    from .schema import prepare_entity
    evidence={'entities':{prepare_entity(e,config)[0]['uri']:[{'kind':'synthetic_example','quote':'这是开发环境中的虚构示例，用于检查搜索、纠错和停用流程。','session_id':'development-demo'}] for e in entities}}
    with KGStore(config) as store:
        return store.write_batch(entities,relations,origin='manual',evidence=evidence,operation_id='development-demo-v1')
