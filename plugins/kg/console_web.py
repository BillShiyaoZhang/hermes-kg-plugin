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
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import load_config
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


def console_token(home):
    value = os.environ.get("HERMES_KG_CONSOLE_TOKEN")
    if value:
        if len(value)<16: raise ValueError('Console access token must contain at least 16 characters')
        return value
    path = Path(home) / "kg-console.token"
    if path.exists():
        value=path.read_text().strip()
        if len(value)<16: raise ValueError('Console token file is incomplete; replace it with a secure token before starting')
        return value
    path.parent.mkdir(parents=True,exist_ok=True)
    value = secrets.token_urlsafe(32)
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w") as file:
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


def create_app(config=None, *, token=None, memory=None, search=None, background=True, static_dir=None):
    config = config or load_config()
    if (config.home/'kg-sync'/'restore-in-progress.json').exists():
        raise ValueError('Restore in progress; finish recovery before starting the workbench')
    if memory is None:
        from .lifecycle import MemoryService
        memory = MemoryService(config)
    if search is None:
        from .search import HybridSearch
        from .embedding import LocalEmbedder
        cache=os.environ.get("HERMES_KG_MODEL_CACHE",str(config.home/"cache"/"kg-models"))
        search = HybridSearch(memory.store,embedder=LocalEmbedder(cache))
    auth_token = token or console_token(config.home)
    state = ConsoleState(config.home)
    stop = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix="kg-console")
    job_lock = threading.Lock()

    def run_job(kind, job_id):
        try:
            if kind == "sync":
                from .runner import run
                code, result = run(home=config.home)
                state.finish(job_id,"succeeded" if code == 0 else "failed",result)
                if code == 0:
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
            if due and not state.get("paused",False) and config.home.joinpath("state.db").exists():
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

    app=FastAPI(title="Hermes Memory Console",version="0.1.0",lifespan=lifespan)
    app.state.memory=memory
    app.state.search=search
    app.state.console_state=state
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","[::1]","testserver"])

    @app.middleware("http")
    async def authorize(request:Request,call_next):
        if request.url.path.startswith("/api/"):
            header=request.headers.get("authorization","")
            if not hmac.compare_digest(header,"Bearer "+auth_token):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail":"需要工作台访问令牌"},401)
            if (config.home/'kg-sync'/'restore-in-progress.json').exists():
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail':'数据恢复尚未完成，请先完成恢复再使用工作台'},503)
            origin=request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail":"拒绝跨站请求"},403)
        response=await call_next(request)
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Content-Security-Policy"]="default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail":"记忆服务暂不可用，请检查数据库和运行状态","error_type":type(exc).__name__},503)

    def invoke(function,*args,**kwargs):
        try:
            return _json(function(*args,**kwargs))
        except (KeyError,LookupError) as exc:
            raise HTTPException(404,"未找到当前 profile 的记忆或待审记录") from exc
        except ValueError as exc:
            message=str(exc)
            conflict=any(word in message.lower() for word in ("version","conflict","revision","concurrent","resolved"))
            raise HTTPException(409 if conflict else 400,message) from exc

    @app.get("/api/v1/status")
    def status():
        from .health import check_health
        try:
            health=_json(check_health(config,memory.store))
        except Exception as exc:
            health={"connected":False,"error_type":type(exc).__name__}
        sync=SyncState(config.home).status()
        sync['pending']=[{key:value for key,value in item.items() if key!='error'} for item in sync.get('pending',[])]
        jobs=state.jobs()
        try:
            search_status=_json(search.status())
        except Exception:
            search_status={"mode":"keyword","model_ready":False}
        return {"ok":True,"connected":health.get("connected",False),"memory":health,
                "sync":{**sync,"paused":state.get("paused",False),"pending_count":len(sync.get("pending",[])),"last_run":state.get("last_sync_at"),"jobs":jobs},
                "search":search_status,"compatibility":{"hermes_source_present":config.home.joinpath("state.db").exists(),"profile":str(config.home),"single_profile":True,"data_mode":"demo" if os.environ.get('HERMES_KG_DEMO_MODE')=='1' else 'personal'}}

    @app.get("/api/v1/memories")
    def memories(type:str|None=None,status:str="active",q:str="",offset:int=0,limit:int=30):
        if status not in {"active","archived","all"} or not 0<=offset or not 1<=limit<=100:
            raise HTTPException(400,"无效的分页或状态")
        items=invoke(memory.list_memories,entity_type=type,include_archived=status!="active",limit=10000)
        if isinstance(items,dict):
            items=items.get("items",[])
        items=[normalize_memory(item) for item in items]
        if status=="archived":
            items=[item for item in items if item["status"]=="archived"]
        if q.strip():
            items=[item for item in items if q.strip().casefold() in json.dumps(item,ensure_ascii=False).casefold()]
        return {"items":items[offset:offset+limit],"total":len(items),"offset":offset,"limit":limit}

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
            canonical_urn(uri,prefix=config.urn_prefix)
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

    @app.get("/api/v1/memories/revisions")
    def revisions(uri:str):
        return {"items":invoke(memory.revisions,uri)}

    def mutate(action,body):
        args=(body.uri,body.changes) if action=="correct" else (body.uri,)
        return invoke(getattr(memory,action),*args,expected_version=body.expected_version,operation_id=body.operation_id,reason=body.reason)

    @app.post("/api/v1/memories/correct")
    def correct(body:MutationBody): return mutate("correct",body)

    @app.post("/api/v1/memories/archive")
    def archive(body:MutationBody): return mutate("archive",body)

    @app.post("/api/v1/memories/restore")
    def restore(body:MutationBody): return mutate("restore",body)

    @app.get("/api/v1/reviews")
    def reviews():
        rows=invoke(memory.reviews)
        from .schema import prepare_entity
        items=[]
        for row in rows:
            entities=[]
            for entity in row.get('payload',{}).get('entities',[]):
                prepared,_=prepare_entity(entity,config)
                uri=prepared['uri']
                evidence=row.get('evidence',{}).get('entities',{}).get(uri,[])
                entities.append({'uri':uri,'type':prepared['type'],'name':entity.get('name',''),
                                 'properties':prepared['properties'],'version':row.get('base_versions',{}).get(uri,0),
                                 'evidence':evidence,'origin':'auto'})
            items.append({**row,'reason':', '.join(row.get('reason_codes',[])),'entities':entities,
                          'relations':row.get('payload',{}).get('relations',[])})
        return {'items':items}

    @app.post("/api/v1/reviews/{review_id}/{action}")
    def review(review_id:str,action:str,body:ReviewBody):
        if action not in {"approve","reject"}:
            raise HTTPException(404,"未知的审核操作")
        return invoke(memory.resolve_review,review_id,action,operation_id=body.operation_id,expected_versions=body.expected_versions,reason=body.reason,changes=body.changes)

    @app.post("/api/v1/sync/run",status_code=202)
    def sync_run():
        if not config.home.joinpath("state.db").exists():
            raise HTTPException(400,"尚未接入 Hermes 会话数据库；请在 Hermes 主机完成 setup")
        return submit("sync")

    @app.post("/api/v1/sync/{action}")
    def sync_control(action:str):
        if action not in {"pause","resume"}:
            raise HTTPException(404,"未知同步操作")
        state.set("paused",action=="pause")
        return {"ok":True,"paused":action=="pause"}

    @app.post("/api/v1/search/reindex",status_code=202)
    def reindex(): return submit("reindex")

    @app.post("/api/v1/search/prepare",status_code=202)
    def prepare(): return submit("prepare")

    @app.get("/api/v1/jobs")
    def jobs(): return {"items":state.jobs()}

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
        return {"profile":str(config.home),"namespace":config.urn_prefix,"user_person_urn":config.user_person_urn,
                "timezone":config.timezone,"default_shared":config.default_shared,"database":config.database,
                "model_route":"Hermes auxiliary.kg_sync（缺省沿用主模型）","embedding":"intfloat/multilingual-e5-small / local CPU",
                "privacy":"数据保留在你的 Hermes 主机；自动提取会发送正文到你配置的模型。停用记忆会保留原始聊天和证据。"}

    folder=Path(static_dir or os.environ.get("HERMES_KG_STATIC_DIR","/app/static"))
    if not folder.is_dir():
        folder=Path(__file__).resolve().parent/'static'
    if not folder.is_dir():
        folder=Path(__file__).resolve().parents[2]/"console"/"dist"
    if folder.is_dir():
        app.mount("/",StaticFiles(directory=str(folder),html=True),name="console")
    else:
        @app.get("/")
        def no_assets():
            return {"product":"Hermes Memory Console","message":"前端尚未构建，请在 console/ 运行 npm ci && npm run build"}
    return app
