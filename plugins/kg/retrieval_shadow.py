"""Authenticated, read-only HTTP boundary for opt-in retrieval experiments.

This module never selects a profile, loads a model, builds an index or starts a
server. Callers inject an already prepared backend and an authority validator.
It is separate from the workbench: disabling its scheduler would not disable
the workbench's mutation endpoints. No candidate text is serialized here; only
documents rebuilt and accepted by the authority may enter an HTTP response.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hmac
import re
from typing import Any, Literal, Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_CANDIDATES = 200
_CONTEXT_FIELDS = (
    'namespace', 'scope', 'schema_version', 'schema_fingerprint',
    'model_fingerprint', 'representation', 'projection_revision', 'index_generation',
)
_STATUS_FIELDS = {
    'ready': bool, 'model_ready': bool, 'model_fingerprint': str, 'model': str,
    'dimensions': int, 'representation': str, 'projection_revision': str,
    'index_generation': str, 'schema_version': int,
}


@dataclass(frozen=True)
class ShadowRequest:
    question: str
    limit: int = 20
    scope: Literal['profile', 'shared'] = 'profile'


class ShadowBackend(Protocol):
    def capture_context(self, *, scope: str) -> Any: ...
    def retrieve(self, request: ShadowRequest, context: Any) -> Sequence[Any]: ...
    def current_context(self, context: Any) -> Any: ...
    def status(self) -> Mapping[str, Any]: ...


class ShadowAuthority(Protocol):
    # The validator must check the expected context before/after authoritative
    # reads, including schema version/hash, and rebuild accepted documents.
    def validate(self, candidates: Sequence[Any], context: Any) -> Any: ...


class _SearchBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    question: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=20, ge=1, le=50)
    scope: Literal['profile', 'shared'] = 'profile'

    @field_validator('question')
    @classmethod
    def nonblank_question(cls, value):
        if not value.strip():
            raise ValueError('A nonblank question is required')
        return value.strip()


def _field(value, key):
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key)


def _context(value):
    fields = {key: _field(value, key) for key in _CONTEXT_FIELDS}
    if type(fields['schema_version']) is not int or fields['schema_version'] < 0:
        raise ValueError('Invalid schema version')
    if fields['scope'] not in {'profile', 'shared'}:
        raise ValueError('Invalid retrieval scope')
    for key in set(_CONTEXT_FIELDS) - {'schema_version'}:
        if not isinstance(fields[key], str) or not 1 <= len(fields[key]) <= 2000:
            raise ValueError('Invalid request context')
    if not fields['namespace'].startswith('urn:') or not fields['namespace'].endswith(':'):
        raise ValueError('Invalid request namespace')
    return fields


def _bounded_sequence(value):
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray))
            or len(value) > MAX_CANDIDATES):
        raise ValueError('Candidate budget exceeded or invalid result')
    return tuple(value)


def _evidence(document):
    # RetrievalDocument.to_dict performs the project's lossless temporal JSON
    # conversion. A backend candidate's serializer is never called.
    data = document.to_dict()
    if not isinstance(data, dict) or data.get('kind') not in {'entity', 'relation', 'ontology'}:
        raise ValueError('Invalid authoritative document')
    if not isinstance(data.get('text'), str) or not isinstance(data.get('metadata'), dict):
        raise ValueError('Invalid authoritative document')
    is_rule = data['kind'] == 'ontology'
    if data['metadata'].get('is_rule') is not is_rule:
        raise ValueError('Authoritative evidence type mismatch')
    return {
        'id': data['id'], 'kind': 'rule' if is_rule else data['kind'], 'is_rule': is_rule,
        'text': data['text'], 'source_key': data['source_key'],
        'source_refs': data['source_refs'],
        'dependency_fingerprint': data['dependency_fingerprint'], 'metadata': data['metadata'],
    }


def _rejected_reasons(rejected):
    result = set()
    for item in rejected:
        reason = item.get('reason') if isinstance(item, Mapping) else None
        result.add(reason if isinstance(reason, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', reason)
                   else 'authority_rejected')
    return sorted(result)


def _response(status, *, context=None, evidence=(), rejected=(), code=None):
    boundaries = {
        **(context or {}), 'read_only': True, 'research_only': True,
        'promotion_eligible': False, 'consistency': 'read_committed',
        'complete': status in {'found', 'empty'},
        'incomplete': status in {'incomplete', 'unavailable'},
    }
    value = {'mode': 'shadow', 'status': status, 'evidence': list(evidence),
             'kg_results': [], 'boundaries': boundaries,
             'rejected_count': len(rejected), 'rejected_reasons': _rejected_reasons(rejected)}
    if code:
        value['errors'] = [{'code': code}]
    return value


def create_shadow_app(*, backend: ShadowBackend, authority: ShadowAuthority, token: str):
    """Build only POST /search and GET /status; no writes or interpretation.

    Each search captures one request context, retrieves candidates under it,
    validates them, then compares the backend's current context as a final
    publish fence. The fence detects observed changes; it is not serializable
    isolation or a promise to revoke evidence after a response has been sent.
    """
    if not isinstance(token, str) or not 16 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise ValueError('An explicit nonblank shadow token of at least 16 characters is required')
    expected_auth = ('Bearer ' + token).encode('utf-8')
    app = FastAPI(title='Read-only Retrieval Shadow', docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        actual = request.headers.get('authorization', '').encode('utf-8')
        if not hmac.compare_digest(actual, expected_auth):
            response = JSONResponse({'error': 'authentication_required'}, status_code=401)
        else:
            response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Pydantic's default error response can echo rejected questions/fields.
        return JSONResponse({'error': 'invalid_request'}, status_code=422)

    @app.get('/status')
    def status():
        try:
            raw = backend.status()
            if not isinstance(raw, Mapping):
                raise ValueError('Invalid backend status')
            public = {}
            for key, expected_type in _STATUS_FIELDS.items():
                value = raw.get(key)
                if type(value) is expected_type and (not isinstance(value, str) or len(value) <= 2000):
                    public[key] = value
            return {'ok': True, 'mode': 'shadow', 'read_only': True,
                    'research_only': True, 'promotion_eligible': False, 'backend': public}
        except Exception:
            return JSONResponse({'ok': False, 'mode': 'shadow', 'read_only': True,
                                 'error': 'shadow_status_unavailable'}, status_code=503)

    @app.post('/search')
    def search(body: _SearchBody):
        fixed = None
        try:
            request = ShadowRequest(body.question, body.limit, body.scope)
            context = backend.capture_context(scope=request.scope)
            fixed = _context(context)
            if fixed['scope'] != request.scope:
                raise ValueError('Captured scope does not match the request')
            candidates = _bounded_sequence(backend.retrieve(request, context))
            if _context(context) != fixed:
                return _response('incomplete', context=fixed, code='request_context_changed')
            result = authority.validate(candidates, context)
            # A publish can occur immediately after authority's final read.
            if _context(context) != fixed or _context(backend.current_context(context)) != fixed:
                return _response('incomplete', context=fixed, code='request_context_changed')
            documents = _bounded_sequence(_field(result, 'documents'))
            rejected = _bounded_sequence(_field(result, 'rejected'))
            result_status, incomplete = _field(result, 'status'), _field(result, 'incomplete')
            if result_status not in {'current', 'incomplete', 'unavailable'} or type(incomplete) is not bool:
                raise ValueError('Invalid authority result')
            if result_status == 'unavailable':
                return JSONResponse(_response('unavailable', context=fixed, rejected=rejected,
                                             code='authority_unavailable'), status_code=503)
            if len(documents) > len(candidates):
                raise ValueError('Authority returned unexpected documents')
            evidence = [_evidence(document) for document in documents[:request.limit]]
            outcome = ('incomplete' if result_status == 'incomplete' or incomplete or rejected
                       else 'found' if evidence else 'empty')
            return _response(outcome, context=fixed, evidence=evidence, rejected=rejected)
        except Exception:
            # No input, candidate text, credential, query or exception message
            # is exposed on unavailable/contract failures.
            return JSONResponse(_response('unavailable', context=fixed,
                                         code='shadow_retrieval_unavailable'), status_code=503)

    return app
