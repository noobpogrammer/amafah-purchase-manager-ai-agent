"""Regression for native WhatsApp IDs incorrectly passed into UUID quote RPCs."""
import os
os.environ.setdefault('SUPABASE_URL','https://localhost.supabase.co')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY','offline-test-key')
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
import openclaw_bridge as bridge

SOURCE='11111111-1111-4111-8111-111111111111'
NATIVE='3EB0A1D3FC1C07609EB480'
BODY='For the 50W LED lights, AED 28 per unit for 10 units. Delivery within 2 days.'

def source_mock(rows):
    q=MagicMock(); q.select.return_value=q; q.eq.return_value=q; q.limit.return_value=q
    q.execute.return_value=SimpleNamespace(data=rows)
    return q

def test_native_id_is_logged_once_and_converted_to_internal_uuid():
    q=source_mock([])
    with patch.object(bridge.db.supabase,'table',return_value=q), patch.object(bridge.db,'log_message',return_value=SOURCE) as log:
        assert bridge._resolve_quote_source('tenant','supplier',NATIVE,BODY)==SOURCE
        assert log.call_args.kwargs['external_message_id']==NATIVE
        assert log.call_args.kwargs['event_key']==f'openclaw:inbound:tenant:supplier:{NATIVE}'
    for field,value in [('client_id','tenant'),('supplier_id','supplier'),('direction','inbound'),('external_message_id',NATIVE)]:
        assert any(c.args==(field,value) for c in q.eq.call_args_list)

@pytest.mark.parametrize('provided',[SOURCE,NATIVE])
def test_existing_inbound_message_is_reused(provided):
    with patch.object(bridge.db.supabase,'table',return_value=source_mock([{'id':SOURCE,'body':BODY}])), patch.object(bridge.db,'log_message') as log:
        assert bridge._resolve_quote_source('tenant','supplier',provided,BODY)==SOURCE
        log.assert_not_called()

@pytest.mark.parametrize('provided,rows,body,status',[
    (SOURCE,[],BODY,422), # unknown, foreign-supplier or outbound UUID cannot be substituted
    (NATIVE,[{'id':SOURCE,'body':'different message'}],BODY,409),
    (NATIVE,[{'id':SOURCE,'body':BODY}]*2,BODY,409),
    (NATIVE,[],None,422),
])
def test_invalid_or_ambiguous_sources_fail_before_quotation_writes(provided,rows,body,status):
    with patch.object(bridge.db.supabase,'table',return_value=source_mock(rows)), patch.object(bridge.db,'log_message') as log:
        with pytest.raises(HTTPException) as error: bridge._resolve_quote_source('tenant','supplier',provided,body)
        assert error.value.status_code==status
        log.assert_not_called()

def test_failed_source_persistence_stops_quotation():
    with patch.object(bridge.db.supabase,'table',return_value=source_mock([])), patch.object(bridge.db,'log_message',return_value=None):
        with pytest.raises(HTTPException) as error:bridge._resolve_quote_source('tenant','supplier',NATIVE,BODY)
        assert error.value.status_code==503

def test_record_quote_passes_internal_source_uuid_to_database():
    req=bridge.RecordQuoteRequest(client_id='tenant',supplier_id='supplier',rfq_id='rfq',source_message_id=NATIVE,raw_message=BODY,variants=[{'price':28,'quantity':10,'delivery_time':'within 2 days'}])
    with patch.object(bridge,'_require_openclaw_key'), patch.object(bridge,'_get_supplier_for_client'), patch.object(bridge,'_get_rfq_and_verify_link',return_value={'status':'active'}), patch.object(bridge,'_require_current_bundle_rfq'), patch.object(bridge,'_resolve_quote_source',return_value=SOURCE), patch.object(bridge.db,'can_record_post_deadline_quote',return_value=False), patch.object(bridge.db,'record_quotes_batch',return_value=[{'id':'quote'}]) as save:
        assert bridge.record_quote(req)['recorded']
        assert save.call_args.kwargs['source_message_id']==SOURCE


def test_inbound_log_keeps_native_id_in_text_column():
    query = MagicMock()
    query.insert.return_value = query
    query.execute.return_value = SimpleNamespace(data=[{'id':SOURCE}])
    with patch.object(bridge.db.supabase,'table',return_value=query), patch.object(bridge.db,'get_message_by_event_key',return_value=None):
        assert bridge.db.log_message('tenant','supplier','inbound',BODY,event_key='event',external_message_id=NATIVE)==SOURCE
    payload=query.insert.call_args.args[0]
    assert payload['external_message_id']==NATIVE
    assert 'id' not in payload
    assert 'sent_at' not in payload
