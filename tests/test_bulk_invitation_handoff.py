"""Exercise the bulk sender against the real final invitation authorization."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import main
import openclaw_bridge as bridge
from supplier_message_planner import BundleItem

class Store:
    def __init__(self, partial=False):
        self.rows = {'supplier_message_bundles': [], 'supplier_message_bundle_items': [],
                     'rfq_suppliers': [{'id':'link','rfq_id':'rfq','supplier_id':'supplier'}],
                     'rfqs': [{'id':'rfq','client_id':'tenant','status':'active','due_by':'2099-01-01T00:00:00Z'}],
                     'message_log': [{'id':'log','sent_at':'2026-11-14T00:00:00Z','external_message_id':'wa-id'}]}
        self.partial = partial
    def table(self, name):
        return Query(self, name)

class Query:
    def __init__(self, store, name):
        self.store, self.name, self.filters = store, name, []
        self.values = None
    def select(self, *_): return self
    def eq(self, key, value): self.filters.append((key,value)); return self
    def limit(self, *_): return self
    def insert(self, values): self.add = values; return self
    def update(self, values): self.values=values; return self
    def execute(self):
        rows=self.store.rows[self.name]
        if hasattr(self,'add'):
            added=self.add if isinstance(self.add,list) else [self.add]
            added=[dict(x,id=x.get('id','bundle')) for x in added]
            rows.extend(added)
            return SimpleNamespace(data=added)
        found=[r for r in rows if all(r.get(k)==v for k,v in self.filters)]
        if self.values is not None:
            if self.store.partial and self.name=='supplier_message_bundle_items': found=[]
            for r in found:r.update(self.values)
        return SimpleNamespace(data=found)

@pytest.mark.asyncio
@pytest.mark.parametrize('partial',[False,True])
async def test_bulk_sender_claims_every_item_before_final_guard(partial):
    store=Store(partial)
    calls=[]
    async def send(phone,message,**kwargs):
        result=bridge.authorize_system_outbound(bridge.SystemOutboundAuthorizationRequest(
            client_id='tenant',phone_number=phone,message=message))
        calls.append(result)
        assert result['authorized'], result
        return True
    with patch.object(main.db,'supabase',store),patch.object(main.db,'is_client_automation_paused',return_value=False),patch.object(main.db,'log_message',return_value='log'),patch.object(main.db,'update_rfq_supplier_sent_message_id'),patch.object(main,'enqueue_message',side_effect=send),patch.object(bridge,'_require_openclaw_key'),patch.object(main.db,'get_supplier_by_id',return_value={'id':'supplier','phone_number':'+123'}),patch.object(main.db,'is_rfq_open',return_value=True):
        result=await main.send_bulk_rfq_bundles('tenant',{'supplier':{'supplier':{'phone_number':'+123'},'items':[{'item':BundleItem(rfq_id='rfq',product_name='lights',quantity=20)}]}})
    if partial:
        assert not calls
        assert result['failed_bundles']
    else:
        assert len(calls)==1
        assert store.rows['supplier_message_bundles'][0]['status']=='sent'
        assert store.rows['supplier_message_bundle_items'][0]['status']=='sent'
