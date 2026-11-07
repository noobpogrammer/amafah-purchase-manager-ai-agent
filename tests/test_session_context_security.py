"""Offline regression tests: never send WhatsApp or query a live database."""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
os.environ.setdefault('SUPABASE_URL', 'https://localhost.supabase.co')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'offline-test-key')
import db
import openclaw_bridge as bridge
from fastapi import HTTPException

class SupplierQuery:
    def __init__(self, rows):
        self.rows, self.filters, self.window = rows, {}, (0, 499)
    def select(self, *a): return self
    def eq(self, k, v): self.filters[k] = v; return self
    def is_(self, k, v): self.filters[k] = None; return self
    def order(self, *a): return self
    def range(self, a, b): self.window = (a,b); return self
    def limit(self, n): self.window=(0,n-1); return self
    def execute(self):
        rows=[r for r in self.rows if all(r.get(k)==v for k,v in self.filters.items())]
        a,b=self.window
        return SimpleNamespace(data=rows[a:b+1])

def supplier(sid, phone, **kw):
    return dict(id=sid, phone_number=phone, client_id='tenant', is_active=True, deleted_at=None, **kw)

def lookup(rows, phone):
    with patch.object(db.supabase, 'table', side_effect=lambda _:SupplierQuery(rows)):
        return db.get_supplier_by_phone('tenant',phone)

def test_number_formatting_still_matches():
    assert lookup([supplier('s','+971 50 123 4567')],'+971501234567')['id']=='s'
    assert lookup([supplier('s','0501234567')],'+971501234567')['id']=='s'

@pytest.mark.parametrize('rows', [
    [supplier('a','+971501234567'),supplier('b','0501234567')],
    [supplier('a','+971 50 123 4567'),supplier('b','+971-50-123-4567')],
    [supplier('a','+971501234567'),supplier('b','+971501234567')],
])
def test_duplicate_numbers_never_pick_first_even_with_exact_match(rows):
    for order in (rows,list(reversed(rows))):
        with pytest.raises(db.AmbiguousSupplierPhoneError):lookup(order,'+971501234567')

def test_inactive_deleted_and_other_business_do_not_conflict():
    good=supplier('good','+971501234567')
    inactive={**good,'id':'inactive','is_active':False}
    deleted={**good,'id':'deleted','deleted_at':'2026-11-01'}
    other={**good,'id':'other','client_id':'other-business'}
    assert lookup([inactive,deleted,other,good],good['phone_number'])['id']=='good'
    assert lookup([inactive,deleted,other],good['phone_number']) is None

def test_duplicate_on_next_page_is_detected():
    rows=[supplier(f'{i:04}',f'+44100{i:07}') for i in range(499)]
    rows += [supplier('match-a','+971501234567'),supplier('match-b','0501234567')]
    with pytest.raises(db.AmbiguousSupplierPhoneError):lookup(rows,'+971501234567')

def test_bridge_reports_conflict_before_writing():
    with patch.object(db,'get_supplier_by_phone',side_effect=db.AmbiguousSupplierPhoneError('conflict')):
        with pytest.raises(HTTPException) as e:bridge._get_supplier_for_client('tenant',None,'+971501234567')
    assert e.value.status_code==409
    assert e.value.detail['code']=='AMBIGUOUS_SUPPLIER_PHONE'

@pytest.mark.parametrize('changes',[{'is_active':False},{'deleted_at':'2026-11-01'}])
def test_supplier_id_uses_active_filters(changes):
    rows=[{**supplier('s','+971501234567'),**changes}]
    with patch.object(db.supabase,'table',side_effect=lambda _:SupplierQuery(rows)):
        with pytest.raises(HTTPException) as e:bridge._get_supplier_for_client('tenant','s',None)
    assert e.value.status_code==404

def chain():
    q=MagicMock()
    for name in ('select','eq','is_','order','limit','in_','neq'):
        getattr(q,name).return_value=q
    q.execute.return_value=SimpleNamespace(data=[])
    return q

@pytest.mark.parametrize('limit,expected',[(0,False),(3,True)])
def test_supplier_history_is_explicit_recovery_only(limit,expected):
    with patch.object(bridge,'_require_openclaw_key'),patch.object(bridge,'_get_supplier_for_client',return_value={'id':'s'}),patch.object(db,'get_open_rfqs_for_supplier',return_value=[]),patch.object(db,'get_supplier_conversation_history',return_value=[{'body':'old'}]) as history,patch.object(db,'get_active_negotiation_session_for_supplier',return_value=None),patch.object(db,'is_client_automation_paused',return_value=False):
        result=bridge.supplier_context(bridge.SupplierContextRequest(client_id='tenant',supplier_id='s',history_limit=limit))
    assert history.called==expected
    assert result['conversation_history']==([{'body':'old'}] if expected else [])
    assert result['history_source']==('database_recovery' if expected else 'openclaw_session')

@pytest.mark.parametrize('include', [False,True])
def test_rfq_lookup_does_not_refetch_history_by_default(include):
    with patch.object(bridge,'_require_openclaw_key'),patch.object(bridge,'_get_supplier_for_client',return_value={'id':'s'}),patch.object(db,'get_open_rfqs_for_supplier',return_value=[{'rfqs':{'id':'rfq','client_id':'tenant','product_name':'Lights'}}]),patch.object(db,'get_active_negotiation_session',return_value=None),patch.object(bridge,'_supplier_bundle_scope',return_value={'rfq_ids':[],'mode':'session','bundle_id':None}),patch.object(db.supabase,'table',side_effect=lambda _:chain()) as table:
        bridge.resolve_rfq_context(bridge.RfqContextResolveRequest(client_id='tenant',supplier_id='s',message='Lights 24',include_history=include))
    names=[c.args[0] for c in table.call_args_list]
    assert ('message_log' in names)==include
