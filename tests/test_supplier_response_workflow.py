from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
import openclaw_bridge as bridge
import buyer_quote_reviews as reviews
from supplier_message_planner import validate_multi_rfq_quotes, AmbiguousSupplierQuote


def test_mixed_reply_preserves_unavailability_and_partial_quantity():
    result = validate_multi_rfq_quotes([
        {'candidate_rfq_id':'panel','confidence':1,'is_available':False,'price':None},
        {'candidate_rfq_id':'flood','confidence':1,'price':26,'delivery_time':'after 4 days'},
        {'candidate_rfq_id':'fan','confidence':1,'price':105,'quantity':2},
    ], ['panel','flood','fan'])
    assert result[0].is_available is False and result[0].price is None
    assert result[1].quantity is None and result[1].delivery_time == 'after 4 days'
    assert result[2].quantity == 2 and result[2].delivery_time is None
    with pytest.raises(AmbiguousSupplierQuote):
        validate_multi_rfq_quotes([{'candidate_rfq_id':'other','confidence':1,'is_available':False}], ['panel'])


def test_invitation_numbering_reaches_analysis_without_native_reply():
    evidence=[{'bundle_id':'old','items':[{'rfq_id':'a','item_position':2},{'rfq_id':'b','item_position':1}]}]
    with patch.object(bridge,'_request_openclaw_analysis',return_value={'items':[]}) as analyze:
        bridge.parse_multi_rfq_quotes('Item 2: 26 AED', [{'id':'a'},{'id':'b'}], evidence)
    payload=analyze.call_args.args[1]
    assert payload['invitation_evidence'] == evidence
    assert all(row['item_number'] is None for row in payload['candidate_rfqs'])


def test_missing_single_selection_cannot_abandon_multi_item_draft():
    with patch.object(bridge,'_require_openclaw_key'), patch.object(bridge,'_get_supplier_for_client'), patch.object(bridge.db,'supabase') as database:
        result=bridge.resolve_commercial_draft(bridge.ResolveCommercialDraftRequest(client_id='c',supplier_id='s',draft_id='d',answer_source_message_id='answer'), 'test')
    assert result['outcome']=='RETRY'
    database.rpc.assert_not_called()


@pytest.mark.parametrize('quantity,flex,min_qty,max_qty,expected',[(2,False,None,None,True),(5,False,None,None,False),(2,True,1,5,False),(2,True,3,5,True),(None,False,None,None,False)])
def test_quantity_decision_respects_existing_buyer_flexibility(quantity,flex,min_qty,max_qty,expected):
    assert reviews.quantity_issue({'quantity':5,'quantity_flexible':flex,'quantity_min':min_qty,'quantity_max':max_qty},{'quantity':quantity}) is expected


def test_recorded_batch_creates_quantity_decisions_without_sending():
    database=MagicMock();database.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data=[{'id':'source'}]
    database.rpc.return_value.execute.return_value.data={'inserted':2}
    links=[{'rfqs':{'id':r,'client_id':'c'}} for r in ('a','b')]
    with patch.object(bridge,'_require_openclaw_key'), patch.object(bridge,'_get_supplier_for_client'),patch.object(bridge.db,'is_client_automation_paused',return_value=False),patch.object(bridge.db,'get_open_rfqs_for_supplier',return_value=links),patch.object(bridge.db,'supabase',database),patch.object(reviews,'ensure_quantity_reviews') as ensure:
        result=bridge.record_multi_item_quotes(bridge.RecordMultiItemQuotesRequest(client_id='c',supplier_id='s',source_message_id='source',raw_message='Panel unavailable, fan AED105 for 2',items=[{'candidate_rfq_id':'a','is_available':False,'confidence':1},{'candidate_rfq_id':'b','price':105,'quantity':2,'confidence':1}]), 'test')
    assert result['inserted']==2 and ensure.call_count==2
    payload=database.rpc.call_args.args[1]['p_items']
    assert payload[0]['price'] is None and payload[0]['is_available'] is False
