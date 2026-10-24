"""Authenticated, loopback-only memory workbench. No Hermes imports at startup."""
from __future__ import annotations

import hmac
import importlib.util
import json
import os
import secrets
import threading
import time
from typing import Literal
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import _boolean,env_value,load_config
from .console_state import ConsoleState
from .sync_state import SyncState


class SearchBody(BaseModel):
    question: str = Field(min_length=1,max_length=4000)
    limit: int = Field(default=20,ge=1,le=50)
    filters: dict = Field(default_factory=dict)
    interpret: bool = False
    scope: Literal['profile','shared'] = 'profile'


class MutationBody(BaseModel):
    uri: str
    expected_version: int = Field(ge=0)
    operation_id: str = Field(min_length=8,max_length=160)
    reason: str = Field(default="",max_length=2000)
    changes: dict = Field(default_factory=dict)


class ReviewBody(BaseModel):
    operation_id: str = Field(min_length=8,max_length=160)
    expected_versions: dict | None = None
    reason: str = Field(default="",max_length=2000)
    changes: dict = Field(default_factory=dict)


class MemoryQueryBody(BaseModel):
    type: str | None = None
    status: Literal['active','archived','all'] = 'active'
    q: str = Field(default='',max_length=4000)
    offset: int = Field(default=0,ge=0,le=1000000)
    limit: int = Field(default=30,ge=1,le=100)


class OntologyQueryBody(BaseModel):
    type: str | None = None
    relation: str | None = None
    q: str = Field(default='',max_length=200)
    offset: int = Field(default=0,ge=0,le=1000000)
    limit: int = Field(default=20,ge=1,le=50)


class DetailBody(BaseModel):
    uri: str = Field(min_length=1,max_length=2000)


class GraphQueryBody(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    q: str = Field(default='',max_length=200)
    types: list[str] = Field(default_factory=list,max_length=128)
    relation_types: list[str] = Field(default_factory=list,max_length=128)
    focus_uri: str | None = Field(default=None,min_length=1,max_length=2000)
    node_limit: int = Field(default=120,ge=1,le=200)
    edge_limit: int = Field(default=240,ge=1,le=500)


class GraphOverviewBody(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    q: str = Field(default='',max_length=200)
    types: list[str] = Field(default_factory=list,max_length=128)
    relation_types: list[str] = Field(default_factory=list,max_length=128)


class GraphNeighborsBody(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    center_uri: str = Field(min_length=1,max_length=2000)
    direction: Literal['in','out','both'] = 'both'
    relation_types: list[str] = Field(default_factory=list,max_length=128)
    cursor: str | None = Field(default=None,min_length=1,max_length=4000)
    limit: int = Field(default=30,ge=1,le=100)


class GraphPathsBody(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    source_uri: str = Field(min_length=1,max_length=2000)
    target_uri: str = Field(min_length=1,max_length=2000)
    mode: Literal['shortest','common_neighbors'] = 'shortest'
    relation_types: list[str] = Field(default_factory=list,max_length=128)
    direction: Literal['out','both'] = 'both'
    max_hops: int = Field(default=6,ge=1,le=6)
    limit: int = Field(default=5,ge=1,le=5)


class ReviewQueryBody(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    q: str = Field(default='',max_length=200)
    offset: int = Field(default=0,ge=0,le=1000000)
    limit: int = Field(default=20,ge=1,le=50)


class OntologyEntityPatch(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    label: str = Field(default=None,min_length=1,max_length=120)
    description: str = Field(default=None,max_length=4000)


class OntologyPropertyPatch(OntologyEntityPatch):
    aliases: list[str] = Field(default=None,max_length=24)
    required_for: list[str] = Field(default=None,max_length=128)
    domain: list[str] = Field(default=None,min_length=1,max_length=128)
    range: list[str] = Field(default=None,min_length=1,max_length=1)


class OntologyRelationPatch(OntologyEntityPatch):
    aliases: list[str] = Field(default=None,max_length=24)
    domain: list[str] = Field(default=None,min_length=1,max_length=128)
    range: list[str] = Field(default=None,min_length=1,max_length=128)


class OntologyPatch(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    entity_types: dict[str,OntologyEntityPatch] = Field(default_factory=dict,max_length=32)
    properties: dict[str,OntologyPropertyPatch] = Field(default_factory=dict,max_length=32)
    relations: dict[str,OntologyRelationPatch] = Field(default_factory=dict,max_length=32)


class OntologyValidateBody(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    expected_namespace: str = Field(min_length=1,max_length=2000)
    expected_version: int = Field(ge=0)
    patch: OntologyPatch = Field(default_factory=OntologyPatch)
    operations: list['OntologyOperation'] = Field(default_factory=list,max_length=32)


class OntologyCreateDefinition(OntologyEntityPatch):
    aliases: list[str] = Field(default=None,max_length=24)
    required_for: list[str] = Field(default=None,max_length=128)
    domain: list[str] = Field(default=None,min_length=1,max_length=128)
    range: list[str] = Field(default=None,min_length=1,max_length=128)
    value_shape: Literal['scalar'] = 'scalar'


class OntologyOperation(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    action: Literal['create','deactivate','restore','delete']
    kind: Literal['entity_types','properties','relations']
    key: str = Field(min_length=1,max_length=64)
    definition: OntologyCreateDefinition | None = None


class OntologyPublishBody(OntologyValidateBody):
    operation_id: str = Field(min_length=8,max_length=160)
    reason: str = Field(default='',max_length=2000)
    confirmation: str | None = Field(default=None,max_length=4000)
    confirmation_token: str | None = Field(default=None,max_length=64)


def console_token(home):
    value = env_value("console_token")
    if value:
        if len(value)<16: raise ValueError('Console access token must contain at least 16 characters')
        return value
    path = Path(home) / "kg-console.token"
    if path.exists():
        value=path.read_text(encoding='utf-8').strip()
        if len(value)<16: raise ValueError('Console token file is incomplete; replace it with a secure token before starting')
        return value
    path.parent.mkdir(parents=True,exist_ok=True)
    value = secrets.token_urlsafe(32)
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w",encoding='utf-8') as file:
        file.write(value)
    return value


def _json(value):
    return json.loads(json.dumps(value,ensure_ascii=False,default=lambda v:v.isoformat() if hasattr(v,"isoformat") else str(v)))


def normalize_memory(item):
    item = _json(item)
    props = item.get("props",item.get("properties",{})) or {}
    return {**item,"properties":props,"props":props,
            "version":item.get('version',item.get('revision')),
            "description":item.get("description") or props.get("hermes_description", ""),
            "status":"archived" if item.get("archived") else item.get("status","active"),
            "type":str(item.get("type","concept")).lower()}


def public_job(job):
    """An explicit diagnostic projection, including for old job ledgers.

    Persisted results may contain upstream exception text or private source
    metadata. In particular, never return the raw result_json SQLite column.
    """
    names={"sync":"记忆同步", "prepare":"准备本地语义模型", "reindex":"更新搜索索引", "index":"更新搜索索引"}
    phases={"queued":"已加入队列", "running":"正在运行", "succeeded":"已完成", "failed":"未完成，可重试", "interrupted":"服务重启后中断，可重试"}
    kind=job.get('kind','')
    status=job.get('status','')
    result=job.get('result') if isinstance(job.get('result'),dict) else {}
    safe_result={key:value for key,value in result.items()
                 if key in {'indexed','vector_indexed','pending','stale_skipped','discovered','committed',
                            'entities_written','relations_written','llm_calls','applied_batches','deferred_batches'}
                 and isinstance(value,(int,float)) and not isinstance(value,bool)}
    summary=f"{names.get(kind,'后台任务')} · {phases.get(status,'状态暂不可用')}"
    if result.get('status') in {'paused','disabled','no_changes','source_changed','setup_incomplete','restore_in_progress'}:
        summary=f"{names.get(kind,'后台任务')} · "+{
            'paused':'自动提取已暂停，本次未处理', 'disabled':'记忆功能已停用，本次未处理',
            'no_changes':'没有需要处理的新内容', 'source_changed':'原始来源发生变化，请先核对',
            'setup_incomplete':'接入尚未完成，请检查集成设置', 'restore_in_progress':'数据恢复尚未完成',
        }[result['status']]
        safe_result['status']=result['status']
    return {**{key:job.get(key) for key in ('id','kind','status','created_at','started_at','finished_at')},
            'summary':summary,'result':safe_result,
            **({'error':'任务未完成；进度已保留，请核对状态后重试。'} if status in {'failed','interrupted'} else {})}


def create_app(config=None, *, token=None, memory=None, search=None, background=True, static_dir=None):
    config = config or load_config()
    automatic_sync = _boolean(env_value('sync_scheduler_enabled','true'),'sync_scheduler_enabled')
    if (config.home/'kg-sync'/'restore-in-progress.json').exists():
        raise ValueError('Restore in progress; finish recovery before starting the workbench')
    if memory is None:
        from .lifecycle import MemoryService
        memory = MemoryService(config)
    if search is None:
        from .search import HybridSearch
        from .embedding import LocalEmbedder
        cache=env_value("model_cache",str(config.home/"cache"/"kg-models"))
        search = HybridSearch(memory.store,embedder=LocalEmbedder(cache))
    auth_token = token or console_token(config.home)
    state = ConsoleState(config.home)
    stop = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix="kg-console")
    job_lock = threading.Lock()

    def run_job(kind, job_id):
        try:
            state.start(job_id)
            if kind == "sync":
                from .runner import run
                code, result = run(home=config.home)
                state.finish(job_id,"succeeded" if code == 0 else "failed",result)
                if code == 0 and result.get('status') not in {'paused','disabled'}:
                    state.set("last_sync_at",time.time())
            else:
                search.ensure_indexes()
                if kind == "prepare":
                    search.embedder.prepare(allow_download=True)
                elif search.embedder is not None and search.embedder.status().get("cached") and not search.embedder.status().get("ready"):
                    search.embedder.prepare(allow_download=False)
                result = search.reindex() if kind in {"prepare","reindex"} else search.index_pending()
                state.finish(job_id,"succeeded",_json(result))
        except Exception as exc:
            state.finish(job_id,"failed",{"error_type":type(exc).__name__,"message":"任务未完成；输入和进度已保留，可重试。"})
        finally:
            job_lock.release()

    def submit(kind):
        if not job_lock.acquire(blocking=False):
            raise HTTPException(409,"已有后台任务运行中，请稍后重试")
        try:
            job_id = state.create_job(kind)
            executor.submit(run_job,kind,job_id)
        except BaseException:
            job_lock.release()
            raise
        if kind == "sync":
            state.set("last_sync_attempt",time.time())
        return {"ok":True,"job_id":job_id,"status":"queued"}

    def scheduler():
        last_index = 0
        while not stop.wait(15):
            now=time.time()
            due = now-state.get("last_sync_attempt",0)>=4*60*60
            if automatic_sync and due and not state.get("paused",False) and config.home.joinpath("state.db").exists():
                try:
                    submit("sync")
                except HTTPException:
                    pass
            if now-last_index>=60:
                try:
                    submit("index")
                    last_index=now
                except HTTPException:
                    pass

    @asynccontextmanager
    async def lifespan(app):
        if background:
            state.recover()
            thread=threading.Thread(target=scheduler,daemon=True,name="kg-scheduler")
            thread.start()
        yield
        stop.set()
        executor.shutdown(wait=False,cancel_futures=True)

    app=FastAPI(title="Memory Workbench",version="0.1.0",lifespan=lifespan)
    app.state.memory=memory
    app.state.search=search
    app.state.console_state=state
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","[::1]","testserver"])

    def secure_response(response):
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Content-Security-Policy"]="default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        return response

    @app.middleware("http")
    async def authorize(request:Request,call_next):
        if request.url.path.startswith("/api/"):
            header=request.headers.get("authorization","")
            if not hmac.compare_digest(header,"Bearer "+auth_token):
                from fastapi.responses import JSONResponse
                return secure_response(JSONResponse({"detail":"需要工作台访问令牌"},401))
            if (config.home/'kg-sync'/'restore-in-progress.json').exists():
                from fastapi.responses import JSONResponse
                return secure_response(JSONResponse({'detail':'数据恢复尚未完成，请先完成恢复再使用工作台'},503))
            origin=request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                from fastapi.responses import JSONResponse
                return secure_response(JSONResponse({"detail":"拒绝跨站请求"},403))
        response=await call_next(request)
        return secure_response(response)

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        from fastapi.responses import JSONResponse
        return secure_response(JSONResponse({"detail":"记忆服务暂不可用，请检查数据库和运行状态","error_type":type(exc).__name__},503))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        from fastapi.responses import JSONResponse
        # Pydantic's default errors include the submitted input, which can
        # repeat credentials or private text in browser diagnostics.
        return secure_response(JSONResponse({'detail':'输入未通过校验，请检查必填项、字段格式和请求范围。'},422))

    def invoke(function,*args,**kwargs):
        try:
            return _json(function(*args,**kwargs))
        except (KeyError,LookupError) as exc:
            raise HTTPException(404,"未找到当前 profile 的记忆或待审记录") from exc
        except ValueError as exc:
            message=str(exc)
            conflict=any(word in message.lower() for word in ("version","conflict","revision","concurrent","resolved"))
            detail='这条记录已发生变化。你的输入未提交，请读取最新版本并重新核对。' if conflict else '输入未通过校验，请检查字段类型、必填项和当前记录状态。'
            if message == 'Ontology alias conflicts with stored literal fields; withdraw conflicting alias before correcting':
                conflict=True
                detail='兼容名称与已保存的字面字段冲突。你的输入未提交，请先在本体管理中撤销冲突兼容名称，再重试更正。'
            if 'already been resolved' in message.lower():
                detail='这批候选已处理。请刷新审阅列表并核对结果。'
            elif 'namespace' in message.lower() or 'outside this profile' in message.lower():
                detail='此操作只允许修改当前工作空间的记录。'
            elif 'already exists with different arguments' in message.lower():
                conflict=True
                detail='此操作标识已用于不同内容。请先核对上次操作结果，再重新提交。'
            raise HTTPException(409 if conflict else 400,detail) from exc

    from .ontology import OntologyService
    ontology = OntologyService(config, memory.store)

    @app.get('/api/v1/ontology')
    def ontology_schema():
        return invoke(ontology.describe)

    def refresh_recall(result, uris=None):
        # Facts are already committed. A derived-index or notification failure
        # must report degradation without disguising the successful receipt.
        report = None
        if uris is not None:
            try:
                refresh = getattr(search, 'index_uris', None)
                report = refresh(list(dict.fromkeys(uris))) if callable(refresh) else {
                    'state': 'degraded', 'lexical': 'degraded', 'semantic': 'degraded',
                    'errors': ['index_refresh_unavailable']}
            except Exception:
                report = {'state': 'degraded', 'lexical': 'degraded', 'semantic': 'degraded',
                          'errors': ['index_refresh_failed']}
        try:
            from .freshness import bump_recall_generation
            bump_recall_generation(config.home)
        except Exception:
            report = {**(report or {}), 'state': 'degraded',
                      'errors': [*(report or {}).get('errors', []), 'recall_notification_failed']}
        return {**result, **({'search_freshness': report} if report is not None else {})}

    def ontology_edit(action, body):
        from .ontology_editing import OntologyEditor, OntologyEditError
        from fastapi.responses import JSONResponse
        try:
            result = getattr(OntologyEditor(config,memory.store),action)(**body.model_dump(exclude_unset=True))
            if action == 'publish' and result.get('ok') is True:
                result = refresh_recall(result)
            return _json(result)
        except OntologyEditError as exc:
            return JSONResponse(exc.response(),status_code=exc.status)

    @app.post('/api/v1/ontology/validate')
    def ontology_validate(body:OntologyValidateBody):
        return ontology_edit('validate',body)

    @app.post('/api/v1/ontology/publish')
    def ontology_publish(body:OntologyPublishBody):
        return ontology_edit('publish',body)

    @app.get('/api/v1/ontology/entities')
    def ontology_entities(type:str|None=None,q:str='',offset:int=0,limit:int=20):
        return invoke(ontology.entities,type=type,q=q,offset=offset,limit=limit)

    @app.get('/api/v1/ontology/relations')
    def ontology_relations(relation:str|None=None,q:str='',offset:int=0,limit:int=20):
        return invoke(ontology.relations,relation=relation,q=q,offset=offset,limit=limit)

    @app.post('/api/v1/ontology/entities/query')
    def ontology_entities_query(body:OntologyQueryBody):
        return ontology_entities(type=body.type,q=body.q,offset=body.offset,limit=body.limit)

    @app.post('/api/v1/ontology/relations/query')
    def ontology_relations_query(body:OntologyQueryBody):
        return ontology_relations(relation=body.relation,q=body.q,offset=body.offset,limit=body.limit)

    @app.post('/api/v1/graph/query')
    def graph_query(body:GraphQueryBody):
        return invoke(ontology.graph,q=body.q,types=body.types,focus_uri=body.focus_uri,
                      relation_types=body.relation_types,node_limit=body.node_limit,edge_limit=body.edge_limit)

    @app.post('/api/v1/graph/overview/query')
    def graph_overview_query(body:GraphOverviewBody):
        return invoke(ontology.graph_overview,**body.model_dump())

    @app.post('/api/v1/graph/neighbors/query')
    def graph_neighbors_query(body:GraphNeighborsBody):
        return invoke(ontology.graph_neighbors,**body.model_dump())

    @app.post('/api/v1/graph/paths/query')
    def graph_paths_query(body:GraphPathsBody):
        return invoke(ontology.graph_paths,**body.model_dump())

    @app.get("/api/v1/status")
    def status():
        from .health import check_health
        try:
            health=_json(check_health(config,memory.store))
        except Exception as exc:
            health={"connected":False,"error_type":type(exc).__name__}
        # Default diagnostics describe capability, not host identity. Memory
        # URIs belong in deliberate record views, never general health dumps.
        for key in ('database','namespace'):
            health.pop(key,None)
        if isinstance(health.get('configured_user'),dict):
            health['configured_user']={'present':health['configured_user'].get('present')}
        try:
            sync=SyncState(config.home).status()
            sync_available=True
        except Exception:
            sync={'pending':[],'queued_sessions':None}
            sync_available=False
        sync['pending']=[{key:value for key,value in item.items() if key!='error'} for item in sync.get('pending',[])]
        try:
            jobs=[public_job(job) for job in state.jobs()]
        except Exception:
            jobs=[]
        try:
            paused=state.get('paused',False)
            last_run=state.get('last_sync_at')
        except Exception:
            paused,last_run=None,None
        try:
            search_status=_json(search.status())
        except Exception:
            search_status={"state":"unavailable","mode":"unavailable","model_ready":None}
        return {"ok":True,"connected":health.get("connected",False),"memory":health,
                "sync":{**sync,"available":sync_available,"source_available":config.home.joinpath('state.db').is_file(),
                        "paused":paused,"scheduler_enabled":automatic_sync,
                        "queued_count":sync.get('queued_sessions'),
                        "pending_count":len(sync.get("pending",[])) if sync_available else None,
                        "failed_count":sum(bool(item.get('attempts')) for item in sync.get('pending',[])) if sync_available else None,
                        "last_run":last_run,"jobs":jobs},
                "search":search_status,"compatibility":{"hermes_source_present":config.home.joinpath("state.db").exists(),"single_profile":True,"data_mode":"demo" if env_value('demo_mode')=='1' else 'personal'}}

    @app.get("/api/v1/memories")
    def memories(type:str|None=None,status:str="active",q:str="",offset:int=0,limit:int=30):
        if status not in {"active","archived","all"} or not 0<=offset<=1000000 or not 1<=limit<=100 or len(q)>4000:
            raise HTTPException(400,"无效的分页或状态")
        items=invoke(memory.list_memories,entity_type=type,include_archived=status!="active",limit=10000)
        if isinstance(items,dict):
            items=items.get("items",[])
        truncated=len(items)>=10000
        items=[normalize_memory(item) for item in items]
        if status=="archived":
            items=[item for item in items if item["status"]=="archived"]
        if q.strip():
            items=[item for item in items if q.strip().casefold() in json.dumps(item,ensure_ascii=False).casefold()]
        return {"items":items[offset:offset+limit],"total":len(items),"offset":offset,"limit":limit,
                "truncated":truncated,"total_is_exact":not truncated}

    @app.post('/api/v1/memories/query')
    def memories_query(body:MemoryQueryBody):
        return memories(type=body.type,status=body.status,q=body.q,offset=body.offset,limit=body.limit)

    @app.post("/api/v1/search")
    def find(body:SearchBody):
        question,filters=body.question,dict(body.filters)
        if body.interpret:
            from .query_planner import interpret_query
            try:
                plan=interpret_query(question,config)
                question=plan['text']
                filters={**plan['filters'],**filters}
            except Exception:
                raise HTTPException(503,'查询理解未完成；可关闭智能理解，直接使用本地搜索')
        if body.scope=='profile':
            filters['namespace']=config.urn_prefix
        result=invoke(search.search,question,limit=body.limit,filters=filters)
        result["kg_results"]=[normalize_memory(row) for row in result.get("kg_results",[])]
        return result

    @app.get("/api/v1/memories/detail")
    def detail(uri:str):
        if not uri.startswith(config.urn_prefix):
            from .schema import ENTITY_LABELS,canonical_urn
            invoke(canonical_urn,uri,prefix=config.urn_prefix)
            rows=invoke(memory.store.run_read,"""MATCH (n {uri:$uri}) WHERE n.shared=true
                AND any(label IN labels(n) WHERE label IN $labels)
                AND NOT n:KGQuarantined AND NOT n:KGLegacyAlias AND NOT n:KGLegacyUnresolved
                RETURN labels(n) AS labels,properties(n) AS properties""",{'uri':uri,'labels':list(ENTITY_LABELS.values())})
            if len(rows)!=1: raise HTTPException(404,'未找到可见的共享记忆')
            item=normalize_memory(memory._card(rows[0]))
            return {**item,'read_only':True,'revisions':[],'relations':[]}
        item=invoke(memory.detail,uri)
        if not item:
            raise HTTPException(404,"未找到记忆")
        item=normalize_memory(item)
        item["revisions"]=invoke(memory.revisions,uri)
        return item

    @app.post('/api/v1/memories/detail')
    def detail_query(body:DetailBody):
        return detail(body.uri)

    @app.get("/api/v1/memories/revisions")
    def revisions(uri:str):
        return {"items":invoke(memory.revisions,uri)}

    def mutate(action,body):
        args=(body.uri,body.changes) if action=="correct" else (body.uri,)
        result = invoke(getattr(memory,action),*args,expected_version=body.expected_version,operation_id=body.operation_id,reason=body.reason)
        if isinstance(result, dict) and result.get('ok') is True and result.get('disposition', 'applied') == 'applied':
            result = refresh_recall(result, [body.uri])
        return result

    @app.post("/api/v1/memories/correct")
    def correct(body:MutationBody): return mutate("correct",body)

    @app.post("/api/v1/memories/archive")
    def archive(body:MutationBody): return mutate("archive",body)

    @app.post("/api/v1/memories/restore")
    def restore(body:MutationBody): return mutate("restore",body)

    def normalized_reviews(rows):
        from .schema import prepare_entity
        from .schema import schema_for_store
        from .ontology_editing import alias_snapshot_state, canonical_input
        ontology = schema_for_store(memory.store)
        items=[]
        for row in rows:
            entities=[]
            frozen = alias_snapshot_state(row.get('ontology_aliases', {}))
            for entity in row.get('payload',{}).get('entities',[]):
                prepared,_=prepare_entity(canonical_input(entity, frozen),config,ontology)
                uri=prepared['uri']
                evidence=row.get('evidence',{}).get('entities',{}).get(uri,[])
                entities.append({'uri':uri,'type':prepared['type'],'name':entity.get('name',''),
                                 'properties':prepared['properties'],'version':row.get('base_versions',{}).get(uri,0),
                                 'evidence':evidence,'origin':'auto'})
            items.append({**row,'reason':', '.join(row.get('reason_codes',[])),'entities':entities,
                          'relations':row.get('payload',{}).get('relations',[])})
        return items

    @app.get("/api/v1/reviews")
    def reviews():
        return {'items':normalized_reviews(invoke(memory.reviews))}

    @app.post('/api/v1/reviews/query')
    def reviews_query(body:ReviewQueryBody):
        result=invoke(memory.query_reviews,q=body.q,offset=body.offset,limit=body.limit)
        return {**result,'items':normalized_reviews(result['items'])}

    @app.post("/api/v1/reviews/{review_id}/{action}")
    def review(review_id:str,action:str,body:ReviewBody):
        if action not in {"approve","reject"}:
            raise HTTPException(404,"未知的审核操作")
        result = invoke(memory.resolve_review,review_id,action,operation_id=body.operation_id,expected_versions=body.expected_versions,reason=body.reason,changes=body.changes)
        if action == 'approve' and isinstance(result, dict) and result.get('ok') is True and result.get('disposition', 'applied') == 'applied':
            uris = list(result.get('entity_uris', []))
            for relation in result.get('relation_ids', []):
                parts = relation.split('|')
                if len(parts) == 3:
                    uris.extend((parts[0], parts[2]))
            result = refresh_recall(result, uris)
        return result

    @app.post("/api/v1/sync/run",status_code=202)
    def sync_run():
        if not config.home.joinpath("state.db").exists():
            raise HTTPException(400,"尚未接入会话来源，请在集成设置中完成来源接入。")
        return submit("sync")

    def sync_control(action:str):
        if action not in {"pause","resume"}:
            raise HTTPException(404,"未知同步操作")
        state.set("paused",action=="pause")
        return {"ok":True,"paused":action=="pause"}

    # Literal control routes must not consume sibling operations such as
    # /sync/backfill before their handler can receive the request.
    @app.post("/api/v1/sync/pause")
    def sync_pause(): return sync_control('pause')

    @app.post("/api/v1/sync/resume")
    def sync_resume(): return sync_control('resume')

    @app.post("/api/v1/search/reindex",status_code=202)
    def reindex(): return submit("reindex")

    @app.post("/api/v1/search/prepare",status_code=202)
    def prepare(): return submit("prepare")

    @app.get("/api/v1/jobs")
    def jobs(): return {"items":[public_job(job) for job in state.jobs()]}

    @app.get('/api/v1/sessions')
    def sessions():
        from .session_source import SessionSource
        if not (config.home/'state.db').is_file(): return {'items':[]}
        return {'items':invoke(SessionSource(config.home).list_sessions,min_messages=1)}

    @app.post('/api/v1/sync/backfill')
    def backfill(body:dict):
        ids=body.get('session_ids')
        if not isinstance(ids,list) or any(not isinstance(v,str) for v in ids):
            raise HTTPException(400,'session_ids 必须是会话 ID 数组')
        return invoke(SyncState(config.home).request_backfill,ids)

    @app.get("/api/v1/settings")
    def settings():
        return {"timezone":config.timezone,"default_shared":config.default_shared,
                "model_route":"由宿主集成提供的抽取模型","embedding":"intfloat/multilingual-e5-small / local CPU",
                "privacy":"数据保留在你部署的记忆服务主机；自动提取会发送正文到你配置的模型。停用记忆会保留原始聊天和证据。"}

    folder=Path(static_dir or env_value("static_dir","/app/static"))
    if not folder.is_dir():
        folder=Path(__file__).resolve().parent/'static'
    if not folder.is_dir():
        folder=Path(__file__).resolve().parents[2]/"console"/"dist"
    if folder.is_dir():
        app.mount("/",StaticFiles(directory=str(folder),html=True),name="console")
    else:
        @app.get("/")
        def no_assets():
            return {"product":"Memory Workbench","message":"前端尚未构建，请在 console/ 运行 npm ci && npm run build"}
    return app
