import os
os.environ.setdefault('SUPABASE_URL','https://localhost.supabase.co')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY','offline-test-key')
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import requests
import operator_jobs as jobs

FLAG={'id':'flag','client_id':'tenant','supplier_id':'supplier','rfq_id':'rfq','status':'pending','metadata':{'authority':'preserve'}}
def query(rows):
    q=MagicMock()
    for name in ['select','eq','in_','order','limit','update']:getattr(q,name).return_value=q
    q.execute.return_value=SimpleNamespace(data=rows)
    return q

def test_enqueue_persists_instruction_and_claim_atomically():
    q=query([FLAG])
    with patch.object(jobs.db.supabase,'table',return_value=q): job=jobs.enqueue(FLAG,' Continue ')
    payload=q.update.call_args.args[0]
    assert payload['status']=='processing'
    assert payload['metadata']['authority']=='preserve'
    assert payload['metadata']['operator_instruction']['instruction']=='Continue'
    assert any(c.args==('status','pending') for c in q.eq.call_args_list)
    assert job['id']==jobs.job_id('tenant','flag','Continue')

def queued():
    return {**FLAG,'status':'processing','metadata':{'operator_instruction':{'id':'job','state':'queued','instruction':'Continue'}}}

def test_slow_processing_is_not_reported_as_sent_or_resolved():
    flag=queued(); receipt=MagicMock(status_code=200);receipt.json.return_value={'job_id':'job','status':'running'}
    with patch.object(jobs.db.supabase,'table',return_value=query([flag])),patch.object(jobs.db,'get_supplier_by_id',return_value={'client_id':'tenant','is_active':True,'phone_number':'+123'}),patch.object(jobs.requests,'post',return_value=receipt),patch.object(jobs,'save_state',return_value=True) as save,patch.object(jobs.db,'complete_flag_operator_action') as complete:
        jobs.sync_jobs('https://offline.invalid','key')
        assert save.call_args.args[1]['state']=='running';complete.assert_not_called()

def test_timeout_keeps_same_job_id_and_does_not_release_claim():
    flag=queued()
    with patch.object(jobs.db.supabase,'table',return_value=query([flag])),patch.object(jobs.db,'get_supplier_by_id',return_value={'client_id':'tenant','is_active':True,'phone_number':'+123'}),patch.object(jobs.requests,'post',side_effect=requests.ReadTimeout()) as post,patch.object(jobs,'save_state',return_value=True) as save,patch.object(jobs.db,'release_flag_claim') as release:
        jobs.sync_jobs('https://offline.invalid','key');jobs.sync_jobs('https://offline.invalid','key')
        assert [c.kwargs['json']['job_id'] for c in post.call_args_list]==['job','job']
        assert save.call_args.args[1]['state']=='queued';release.assert_not_called()

def test_completion_resolves_only_after_confirmed_receipt():
    flag=queued();flag['metadata']['operator_instruction']['state']='running'
    receipt=MagicMock(status_code=200);receipt.json.return_value={'job_id':'job','status':'completed'}
    with patch.object(jobs.db.supabase,'table',return_value=query([flag])),patch.object(jobs.requests,'get',return_value=receipt),patch.object(jobs,'save_state',return_value=True),patch.object(jobs.db,'complete_flag_operator_action') as complete:
        jobs.sync_jobs('https://offline.invalid','key');complete.assert_called_once_with('flag','tenant','Continue')

def test_unknown_receipt_is_visible_and_never_blindly_retried():
    flag=queued();flag['metadata']['operator_instruction']['state']='unknown'
    with patch.object(jobs.db.supabase,'table',return_value=query([flag])),patch.object(jobs.requests,'post') as post,patch.object(jobs.requests,'get') as get:
        jobs.sync_jobs('https://offline.invalid','key');post.assert_not_called();get.assert_not_called()

def test_api_accepts_queue_without_waiting_for_ai_or_claiming_delivery():
    import main
    from fastapi.testclient import TestClient
    client=TestClient(main.app)
    main.app.dependency_overrides[main.get_current_user]=lambda:{'client_id':'tenant'}
    try:
        flag={**FLAG,'suppliers':{'phone_number':'+123'}}
        with patch.object(main.db,'get_flag_by_id',return_value=flag),patch.object(jobs,'enqueue',return_value={'id':'job','state':'queued'}),patch.object(requests,'post') as post:
            response=client.post('/flags/flag/respond',json={'response':'Continue','send_to_supplier':True})
            assert response.status_code==202
            assert response.json()['sent_to_supplier'] is False
            assert response.json()['instruction_status']=='queued'
            post.assert_not_called()
    finally:main.app.dependency_overrides.clear()

def test_api_duplicate_request_returns_existing_job_without_requeue():
    import main
    from fastapi.testclient import TestClient
    main.app.dependency_overrides[main.get_current_user]=lambda:{'client_id':'tenant'}
    flag=queued();flag['metadata']['operator_instruction']['id']=jobs.job_id('tenant','flag','Continue')
    try:
        with patch.object(main.db,'get_flag_by_id',return_value=flag),patch.object(jobs,'enqueue') as enqueue:
            response=TestClient(main.app).post('/flags/flag/respond',json={'response':'Continue','send_to_supplier':True})
            assert response.status_code==202;enqueue.assert_not_called()
    finally:main.app.dependency_overrides.clear()


def test_missing_receipt_is_uncertain_and_never_resubmitted():
    flag=queued();flag['metadata']['operator_instruction']['state']='running'
    with patch.object(jobs.db.supabase,'table',return_value=query([flag])),patch.object(jobs.requests,'get',return_value=MagicMock(status_code=404)),patch.object(jobs.requests,'post') as post,patch.object(jobs,'save_state',return_value=True) as save:
        jobs.sync_jobs('https://offline.invalid','key')
        assert save.call_args.args[1]['state']=='unknown';post.assert_not_called()
