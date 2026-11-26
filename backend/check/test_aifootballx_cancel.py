from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api import api_aifootballx as api
from backend.system.system_aifootballx import DeliveryService, JobStore
from backend.system.system_firestore import BackendError
from backend.system import system_aifootballx as delivery


def test_cancel_skips_queued_callback_and_preserves_running_job(tmp_path):
    svc = DeliveryService(JobStore(tmp_path))
    svc.pool.shutdown()
    scope = {'seasonId': '20212022', 'mode': 'matches', 'roundFrom': 1, 'roundTo': 20}
    first, _ = svc.store.create('delivery', scope, 'operator')
    assert svc.store.claim(first)
    second, _ = svc.store.create('delivery', {**scope, 'roundTo': 15}, 'operator')
    svc.store.cancel_queued(second, 'operator')
    svc.store.cancel_queued(second, 'operator')
    svc.run_scope_send(second, 'operator')  # Already-submitted executor callback is harmless.
    assert svc.store.job(first, 'operator')['state'] == 'running'
    assert svc.store.job(second, 'operator')['state'] == 'cancelled'
    replacement, created = svc.store.create('delivery', {**scope, 'roundTo': 15}, 'operator', deduplicate_active=True)
    assert created and replacement != second


def test_cancel_and_worker_claim_are_atomic(tmp_path):
    store = JobStore(tmp_path)
    ident, _ = store.create('delivery', {'mode': 'matches'}, 'operator')
    gate = Barrier(2)
    def claim():
        gate.wait()
        return store.claim(ident)
    def cancel():
        gate.wait()
        try:
            store.cancel_queued(ident, 'operator')
            return True
        except BackendError as exc:
            assert exc.status_code == 409
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed, cancelled = pool.submit(claim), pool.submit(cancel)
        assert claimed.result() != cancelled.result()
    assert store.job(ident, 'operator')['state'] in ('running', 'cancelled')


def test_cancel_api_targets_owned_queued_job_only(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    monkeypatch.setattr(api, 'delivery_service', lambda: SimpleNamespace(store=store))
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    app.dependency_overrides[api.delivery_operator] = lambda: SimpleNamespace(uid='operator')
    @app.exception_handler(BackendError)
    async def error(_, exc):
        return JSONResponse({'error': exc.code}, status_code=exc.status_code)
    client = TestClient(app)
    ident, _ = store.create('delivery', {'mode': 'matches'}, 'operator')
    other, _ = store.create('delivery', {'mode': 'matches'}, 'other')
    private, _ = store.create('preview', {'mode': 'matches'}, 'operator', public=False)
    for hidden in (other, private, 'missing'):
        assert client.post('/api/v1/aifootballx/send/cancel', data={'job_id': hidden}).status_code == 404
    for _ in range(2):
        response = client.post('/api/v1/aifootballx/send/cancel', data={'job_id': ident})
        assert response.json() == {'jobId': ident, 'status': 'cancelled'}
    for state in ('running', 'complete', 'failed', 'partial', 'invalid'):
        store.set_job(ident, state)
        assert client.post('/api/v1/aifootballx/send/cancel', data={'job_id': ident}).status_code == 409
        assert store.job(ident, 'operator')['state'] == state
    assert client.post('/api/v1/aifootballx/send/cancel').status_code == 422
    app.dependency_overrides[api.delivery_operator] = lambda: (_ for _ in ()).throw(BackendError('Forbidden', 403, 'forbidden'))
    assert client.post('/api/v1/aifootballx/send/cancel', data={'job_id': ident}).status_code == 403


def test_cancelled_status_exposes_target_id(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, 'get_settings', lambda: SimpleNamespace(aifootballx_write_enabled=True))
    svc = DeliveryService(JobStore(tmp_path))
    try:
        assert svc.public_status('operator')['jobId'] is None
        for kind, mode in [('delivery', 'matches'), ('goalx_rebuild', 'goalx_rebuild')]:
            ident, _ = svc.store.create(kind, {'mode': mode}, 'operator')
            svc.store.cancel_queued(ident, 'operator')
            status = svc.public_status('operator')
            assert status['jobId'] == ident and status['status'] == 'cancelled'
            assert status['percent'] == 0
    finally:
        svc.close()


def test_shell_helper_works_without_backend_imports_and_refuses_running(tmp_path):
    store = JobStore(tmp_path)
    ident, _ = store.create('delivery', {'mode': 'matches'}, 'operator')
    script = Path(__file__).resolve().parents[1] / 'build/cancel_aifootballx_queued.py'
    command = [sys.executable, '-I', str(script), '--db', str(store.path)]
    listed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert ident in listed.stdout and store.job(ident, 'operator')['state'] == 'queued'
    subprocess.run([*command, '--cancel', ident], capture_output=True, check=True)
    assert store.job(ident, 'operator')['state'] == 'cancelled'
    running, _ = store.create('delivery', {'mode': 'matches'}, 'operator')
    assert store.claim(running)
    rejected = subprocess.run([*command, '--cancel', running], capture_output=True)
    assert rejected.returncode != 0 and store.job(running, 'operator')['state'] == 'running'
