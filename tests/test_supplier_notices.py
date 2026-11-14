from unittest.mock import patch,MagicMock,AsyncMock
from types import SimpleNamespace
import pytest
import supplier_notices as notices

def query(rows):
    q=MagicMock()
    for method in ['select','eq','order','limit','update']:getattr(q,method).return_value=q
    q.execute.return_value=SimpleNamespace(data=rows)
    return q

@pytest.mark.asyncio
@pytest.mark.parametrize('accepted,sent',[(False,True),(True,True),(False,False)])
async def test_adjacent_closures_share_one_delivery_and_keep_accepted_deal_out(accepted,sent):
    pending=query([{'client_id':'c','supplier_id':'s'}]);updates=query([])
    rows=[{'id':'one','rfq_id':'r1','batch_id':'batch'},{'id':'two','rfq_id':'r2','batch_id':'batch'}]
    rpc=MagicMock();rpc.execute.return_value.data=rows
    sender=AsyncMock(return_value=sent)
    def table(name):return pending if name=='supplier_closure_outbox' else updates
    with patch.object(notices.db.supabase,'table',side_effect=table),patch.object(notices.db.supabase,'rpc',return_value=rpc),patch.object(notices.db,'is_client_automation_paused',return_value=False),patch.object(notices.db,'get_supplier_by_id',return_value={'is_active':True,'phone_number':'+123'}),patch.object(notices.db,'get_rfq_by_id',side_effect=lambda r,**_: {'id':r,'product_name':'Drill' if r=='r1' else 'Screwdriver','status':'closed','quantity':20 if r=='r1' else 5}),patch.object(notices.db,'supplier_has_accepted_quote',side_effect=lambda r,s:accepted and r=='r1'),patch.object(notices.db,'log_message',return_value='log'):
        await notices.flush_closures(sender)
    sender.assert_awaited_once()
    text=sender.call_args.args[1]
    assert 'Screwdriver' in text
    assert ('Drill' in text) is (not accepted)
    assert 'Ref:' not in text
    statuses=[c.args[0]['status'] for c in pending.update.call_args_list if 'status' in c.args[0]]
    assert ('sent' if sent else 'unknown') in statuses

@pytest.mark.asyncio
async def test_paused_client_does_not_claim_or_send():
    with patch.object(notices.db.supabase,'table',return_value=query([{'client_id':'c','supplier_id':'s'}])),patch.object(notices.db,'is_client_automation_paused',return_value=True),patch.object(notices.db.supabase,'rpc') as claim:
        sender=AsyncMock();await notices.flush_closures(sender)
    claim.assert_not_called();sender.assert_not_awaited()


@pytest.mark.parametrize('percent,count,expected',[(49,0,None),(50,0,('50%',1)),(75,0,('70%',2)),(95,0,('90%',3)),(96,3,None),(100,0,None),(75,2,None)])
def test_no_reminder_catchup_burst(percent,count,expected):
    assert notices.due_reminder_stage(percent,count)==expected
