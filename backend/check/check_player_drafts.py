
from hashlib import sha256
import json
from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient
from openpyxl import Workbook
from pydantic import SecretStr

from backend.check.check_player_requests import Collection, Reference, Snapshot, Transaction, db, manager
from backend.system import system_player_drafts as drafts


def test_import_edit_and_apply_number_atomically(db, monkeypatch):
    monkeypatch.setattr(drafts, 'get_firestore_client', lambda: db)
    monkeypatch.setattr(drafts, 'get_settings', lambda: SimpleNamespace(jpd_did_storage_bucket='test-bucket'))
    monkeypatch.setattr(drafts, 'ensure_firebase_app', lambda: None)
    stream = Collection.stream

    def reject_player_scan(collection, *args, **kwargs):
        if collection.path in {'players', 'teams'}:
            assert collection.predicate is not None, 'Comparison must query only the relevant player and team candidates'
        return stream(collection, *args, **kwargs)

    monkeypatch.setattr(Collection, 'stream', reject_player_scan)
    monkeypatch.setattr(Reference, 'create', Reference.set, raising=False)
    monkeypatch.setattr(Reference, 'update', lambda self, patch: self.set({**self.get().to_dict(), **patch}), raising=False)
    monkeypatch.setattr(Snapshot, 'update_time', property(lambda self: str(self.value)), raising=False)
    job_id = 'transfer-ops-7-12345678901234567890'
    path = f'playerDrafts/{job_id}/1.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.title = '26-27 roster'
    sheet.append(['p_id', 'p_name', 'Full-name', 'p_birth', 'p_number', 'p_position', 'number_history'])
    sheet.append(['S-123', '선수', 'Test Player', '2000.01.01', 19, 'MF', 'No.9-26.10.17 / No.19-26.10.21'])
    sheet.append(['S-456', 'Other', 'Other Player', '2000.01.01', 8, 'MF', ''])
    buffer = BytesIO()
    book.save(buffer)
    contents = buffer.getvalue()
    blob = SimpleNamespace(size=len(contents), generation=1, download_as_bytes=lambda **_: contents)
    monkeypatch.setattr(drafts.storage, 'bucket', lambda *args, **kwargs: SimpleNamespace(get_blob=lambda key: blob if key == path else None))
    db.documents.update({
        f'playerDrafts/{job_id}': {
            'status': 'pending', 'schemaVersion': 1, 'bucket': 'test-bucket', 'memo': 'saved memo',
            'files': [{'team': '세비야', 'path': path, 'sha256': sha256(contents).hexdigest()}],
            'targets': [{'id': 'roster-5', 'action': 'number', 'kind': 'shirt_number', 'team': '세비야',
                         'code': 'S-123', 'nameFull': 'Test Player', 'effectiveDate': '2026-10-17', 'number': '9'}],
        },
        'players/P': {'name': '선수', 'nameFull': 'Test Player', 'birth': '2000.01.01', 'currentTeamId': 'T', 'currentNo': '7'},
        'players/P/contracts/old': {'teamId': 'T', 'leagueId': 'LALIGA', 'competitionType': 'league', 'from': '2026.07.01', 'to': None, 'no': '7', 'pos': 'MF'},
        'teams/T/squad/P': {'no': '7', 'contractId': 'old'},
    })
    service = drafts.PlayerDraftService()
    steps = service.compare_steps(job_id)
    assert next(steps) == {'done': 0, 'total': 1}
    assert service.items(job_id) == []
    assert next(steps) == {'done': 1, 'total': 1}
    [item] = service.items(job_id)
    assert list(steps) == []
    assert db.documents[f'playerDrafts/{job_id}']['status'] == 'compared'
    assert item['status'] == 'changed' and item['playerId'] == 'P'
    assert item['excel']['no'] == '9'
    assert service.details(job_id)['memo'] == 'saved memo'

    edited = service.edit(job_id, item['id'], drafts.DraftEdit(version=item['version'], playerId='P',
        values=drafts.DraftValues(**{**item['values'], 'no': '11'})), manager)
    assert edited['excel']['no'] == '9'
    assert db.documents['players/P']['currentNo'] == '7'

    db.documents['players/P']['nameEn'] = 'T. Player'
    assert service.apply(job_id, item['id'], edited['version'], manager)['status'] == 'stale'
    [refreshed] = service.compare(job_id)
    assert refreshed['values']['no'] == '11'
    result = service.apply(job_id, item['id'], refreshed['version'], manager)
    assert result['status'] == 'applied'
    assert db.documents['players/P']['currentNo'] == '11'
    assert db.documents['players/P/contracts/old']['to'] == '2026.10.16'
    assert db.documents['teams/T/squad/P']['no'] == '11'
    assert service.apply(job_id, item['id'], refreshed['version'], manager) == result



def test_candidate_lookup_uses_normalized_names_and_exact_contract_aliases(db):
    db.documents.update({
        'players/P': {'name': '선수', 'nameFull': 'José Salinas', 'nameFullKey': drafts.normalize('José Salinas')},
        'players/Q': {'name': '다른 선수', 'nameFull': 'Other Player'},
        'players/P/contracts/old': {'teamId': 'T', 'alias': 'S-ES6, S-ES69'},
    })
    players = db.collection('players')
    assert [candidate['id'] for candidate in drafts._player_candidates(players, 'Jose Salinas', '')] == ['P']
    player = players.document('P')
    assert drafts._matches_contract_alias(player, 's-es69', 'T')
    assert not drafts._matches_contract_alias(player, 'S-ES', 'T')
    assert not drafts._matches_contract_alias(player, 'S-ES69', 'other-team')



def test_delete_item_and_job_remove_only_inbox_data(db, monkeypatch):
    monkeypatch.setattr(drafts, 'get_firestore_client', lambda: db)
    monkeypatch.setattr(Collection, 'limit', lambda collection, count: collection, raising=False)
    monkeypatch.setattr(Transaction, 'delete',
                        lambda tx, ref: tx.writes.append(('delete', ref.path, {}, False)), raising=False)
    commit = Transaction.commit

    def commit_with_delete(tx):
        deletes = [path for operation, path, _, _ in tx.writes if operation == 'delete']
        tx.writes = [write for write in tx.writes if write[0] != 'delete']
        commit(tx)
        for path in deletes:
            tx.db.documents.pop(path, None)

    monkeypatch.setattr(Transaction, 'commit', commit_with_delete)
    job = 'transfer-ops-7-12345678901234567890'
    path = f'playerDrafts/{job}'
    kept = {'id': 'transfer-2', 'nameFull': 'Kept Player', 'team': 'Team'}
    db.documents.update({
        path: {'status': 'pending', 'targets': [{'id': 'transfer-1', 'nameFull': 'Deleted Player'}, kept],
               'memo': 'Deleted Player and Kept Player', 'extracted': {'transfer-1': {}, 'transfer-2': {}}},
        f'{path}/items/transfer-1': {'status': 'applied'},
        f'{path}/items/transfer-2': {'status': 'review'},
        'players/P': {'name': 'Kept Profile'},
        'players/P/contracts/C': {'teamId': 'T'},
        'playerDrafts/other': {'status': 'pending'},
    })
    service = drafts.PlayerDraftService()
    service.delete_item(job, 'transfer-1')
    assert f'{path}/items/transfer-1' not in db.documents
    assert db.documents[path]['targets'] == [kept]
    assert db.documents[path]['extracted'] == {'transfer-2': {}}
    assert 'Deleted Player' not in db.documents[path]['memo']
    service.delete_job(job)
    service.delete_job(job)
    assert not any(key == path or key.startswith(path + '/') for key in db.documents)
    assert db.documents['players/P']['name'] == 'Kept Profile'
    assert db.documents['players/P/contracts/C']['teamId'] == 'T'
    assert 'playerDrafts/other' in db.documents



def test_recompare_reopens_previously_applied_transfer_date_correction(db, monkeypatch):
    from backend.api.api_player_drafts import BulkApplyItem, apply_job

    monkeypatch.setattr(drafts, 'get_firestore_client', lambda: db)
    monkeypatch.setattr(Snapshot, 'update_time', property(lambda self: str(self.value)), raising=False)
    monkeypatch.setattr(drafts, '_today', lambda: drafts.date(2026, 11, 11))
    job = 'transfer-ops-9-12345678901234567890'
    path = f'playerDrafts/{job}'
    target = {'id': 'transfer-6', 'action': 'team', 'kind': '이적', 'effectiveDate': '2026-07-23'}
    values = drafts.DraftValues(name='E.앤더슨', nameEn='E.Anderson', nameFull='Elliot Anderson',
                               effectiveDate='2026-07-23', teamId='T', no='5', pos='MF').model_dump()
    assert values['nameEn'] == 'E. Anderson'
    db.documents.update({
        path: {'schemaVersion': 2, 'targets': [target], 'extracted': {}, 'status': 'compared'},
        f'{path}/items/transfer-6': {'status': 'applied', 'version': 3, 'target': target, 'values': values,
            'current': {'teamId': 'T', 'effectiveDate': '2026-07-02'}, 'excel': values,
            'playerId': 'P', 'sourceTeamId': 'N', 'registerNew': False},
        'players/P': {'name': 'E.앤더슨', 'nameEn': 'E.Anderson', 'nameFull': 'Elliot Anderson', 'currentTeamId': 'T'},
        'players/P/contracts/current': {'teamId': 'T', 'leagueId': 'LALIGA', 'from': '2026.07.02', 'to': None,
                                       'no': '5', 'pos': 'MF'},
        'players/P/contracts/prior': {'teamId': 'N', 'from': '2024.07.01', 'to': '2026.07.01'},
    })
    service = drafts.PlayerDraftService()
    [correction] = service.compare(job)
    assert correction['status'] == 'changed'
    assert correction['reason'] == '적용일 2026-07-02 → 2026-07-23'
    result = apply_job(job, manager, [BulkApplyItem(id='transfer-6', version=correction['version'])])
    assert result['results'][0]['status'] == 'applied'
    assert db.documents['players/P/contracts/current']['from'] == '2026.07.23'
    assert db.documents['players/P/contracts/prior']['to'] == '2026.07.22'
    assert db.documents['teams/T/squad/P']['since'] == '2026.07.23'
    assert service.compare(job)[0]['status'] == 'applied'


def test_future_original_cannot_be_approved_with_an_earlier_edited_date(monkeypatch):
    monkeypatch.setattr(drafts, '_today', lambda: drafts.date(2026, 11, 11))
    target = {'action': 'team', 'kind': '임대 복귀', 'effectiveDate': '2027-06-30'}
    values = {'effectiveDate': '2026-07-27', 'teamId': 'S-ES'}
    status, reason = drafts._classify(target, {}, values, {}, [], False, 'S-MA')
    assert status == 'review'
    assert '원본이 미래 예정 이적' in reason



def test_two_player_delivery_can_be_compared_and_applied_together(db, monkeypatch):
    from backend.api.api_player_drafts import BulkApplyItem, apply_job

    monkeypatch.setattr(drafts, 'get_firestore_client', lambda: db)
    monkeypatch.setattr(Reference, 'create', Reference.set, raising=False)
    monkeypatch.setattr(Snapshot, 'update_time', property(lambda self: str(self.value)), raising=False)
    monkeypatch.setattr(drafts, '_today', lambda: drafts.date(2026, 11, 11))
    book = Workbook()
    sheet = book.active
    sheet.title = '26-27 roster'
    sheet.append(['p_id', 'p_name', 'Full-name', 'p_birth', 'p_number', 'p_position'])
    targets = []
    for i in (1, 2):
        player_id, full_name, code = f'P{i}', f'Fixture Player {i}', f'S-TEST{i}'
        sheet.append([code, f'Test {i}', full_name, '2000.01.01', 10 + i, 'MF'])
        db.documents[f'players/{player_id}'] = {'nameFull': full_name, 'birth': '2000.01.01', 'currentTeamId': 'T'}
        db.documents[f'players/{player_id}/contracts/open'] = {
            'teamId': 'T', 'leagueId': 'LALIGA', 'from': '2026.07.01', 'to': None, 'no': '4', 'pos': 'MF',
        }
        targets.append({'id': f'roster-{i}', 'action': 'number', 'kind': 'shirt_number', 'team': '세비야',
                        'teamCode': 'T', 'code': code, 'nameFull': full_name, 'effectiveDate': '2026-07-01', 'number': str(10 + i)})
    buf = BytesIO()
    book.save(buf)
    workbook = buf.getvalue()
    upload = SimpleNamespace(filename='test.xlsx', size=len(workbook), file=BytesIO(workbook))
    manifest = json.dumps({
        'schemaVersion': 2, 'source': 'transfer-ops', 'runId': 123, 'targets': targets,
        'files': [{'name': upload.filename, 'team': '세비야', 'sha256': sha256(workbook).hexdigest()}],
    })
    service = drafts.PlayerDraftService()
    job_id = service.receive(manifest, [upload])['job_id']
    assert service.receive(manifest, [upload])['job_id'] == job_id
    assert list(service.compare_steps(job_id)) == [{'done': n, 'total': 2} for n in range(3)]
    items = service.items(job_id)
    assert [item['status'] for item in items] == ['changed', 'changed']
    reviewed = [BulkApplyItem(id=item['id'], version=item['version']) for item in items]
    result = apply_job(job_id, manager, reviewed)
    assert [item['status'] for item in result['results']] == ['applied', 'applied']
    assert [db.documents[f'players/P{i}']['currentNo'] for i in (1, 2)] == ['11', '12']


def test_render_receive_checks_token_and_persists_excel_values_once(db, monkeypatch):
    from backend.api import api_player_drafts
    from backend.app import app

    monkeypatch.setattr(api_player_drafts, 'get_settings',
                        lambda: SimpleNamespace(jpd_did_transfer_token=SecretStr('transfer-key')))
    monkeypatch.setattr(drafts, 'get_firestore_client', lambda: db)
    monkeypatch.setattr(Reference, 'create', Reference.set, raising=False)
    book = Workbook()
    sheet = book.active
    sheet.title = '26-27 roster'
    sheet.append(['p_id', 'p_name', 'Full-name', 'p_birth', 'p_number', 'p_position'])
    sheet.append(['S-123', '선수', 'Test Player', '2000.01.01', 9, 'MF'])
    buffer = BytesIO()
    book.save(buffer)
    contents = buffer.getvalue()
    manifest = json.dumps({
        'schemaVersion': 2, 'source': 'transfer-ops', 'runId': 8, 'memo': '확정 작업 메모',
        'files': [{'name': '확정.xlsx', 'team': '세비야', 'sha256': sha256(contents).hexdigest()}],
        'targets': [{'id': 'roster-5', 'action': 'number', 'kind': 'shirt_number', 'team': '세비야',
                     'code': 'S-123', 'effectiveDate': '2026-10-17', 'number': '9'}],
    }, ensure_ascii=False, sort_keys=True)
    body = {'data': {'manifest': manifest}, 'files': [('files', ('확정.xlsx', contents))]}
    client = TestClient(app)
    route = '/api/v1/player-drafts/receive'
    assert client.post(route, **body).status_code == 401
    assert not any(key.startswith('playerDrafts/') for key in db.documents)
    auth = {'Authorization': 'Bearer transfer-key'}
    wrong_file = {'data': body['data'], 'files': [('files', ('확정.xlsx', b'wrong'))]}
    assert client.post(route, headers=auth, **wrong_file).status_code == 422
    assert not any(key.startswith('playerDrafts/') for key in db.documents)
    response = client.post(route, headers=auth, **body)
    assert response.status_code == 201, response.text
    job_id = response.json()['job_id']
    stored = db.documents[f'playerDrafts/{job_id}']
    assert stored['memo'] == '확정 작업 메모' and stored['status'] == 'pending'
    assert stored['extracted']['roster-5']['values']['no'] == '9'
    assert client.post(route, headers=auth, **body).json() == {'job_id': job_id}
    assert list(key for key in db.documents if key.startswith('playerDrafts/')) == [f'playerDrafts/{job_id}']
    monkeypatch.setattr(Collection, 'select', lambda self, fields: self)
    monkeypatch.setattr(Reference, 'update', lambda self, patch: self.set({**self.get().to_dict(), **patch}), raising=False)
    def no_storage(*args, **kwargs):
        raise AssertionError('The API delivery must not use Storage')
    monkeypatch.setattr(drafts.storage, 'bucket', no_storage)
    [result] = drafts.PlayerDraftService().compare(job_id)
    assert result['excel']['no'] == '9' and result['status'] == 'review'
