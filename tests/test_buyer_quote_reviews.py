from unittest.mock import patch,MagicMock
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
import main
import buyer_quote_reviews as reviews

OFFER={'id':'quote','supplier_id':'supplier','price':95,'quantity':5,'resolved_delivery_days':3,'quality_notes':'As specified','term_sources':{}}
REVIEW={'id':'review','client_id':'tenant','supplier_id':'supplier','rfq_id':'rfq','quote_id':'quote','status':'pending','offer_snapshot':reviews.snapshot(OFFER),'job_id':'job','job_state':None}

@pytest.mark.asyncio
async def test_member_cannot_approve_final_offer():
    with pytest.raises(HTTPException) as exc:
        await main.decide_final_offer('rfq','review',main.BuyerFinalOfferDecision(decision='accept'),{'client_id':'tenant','role':'member'})
    assert exc.value.status_code==403

@pytest.mark.asyncio
@pytest.mark.parametrize('changed,decision,counter,status',[(True,'accept',None,409),(False,'negotiate',100,422),(False,'negotiate',90,None),(False,'accept',None,None)])
async def test_buyer_decision_validates_exact_offer_and_counter(changed,decision,counter,status):
    offer={**OFFER,'price':96 if changed else 95}
    fake=MagicMock();fake.rpc.return_value.execute.return_value.data={**REVIEW,'status':'authorized'}
    with patch.object(reviews,'list_reviews',return_value=[REVIEW]),patch.object(main.openclaw_bridge,'_current_offer',return_value=offer),patch.object(main.db,'get_rfq_by_id',return_value={'id':'rfq','status':'active','acceptable_price_min':80,'acceptable_price_max':100}),patch.object(main.db,'get_rfq_negotiation_constraints',return_value=[]),patch.object(main.db,'supabase',fake):
        req=main.BuyerFinalOfferDecision(decision=decision,counter_price=counter)
        if status:
            with pytest.raises(HTTPException) as exc:await main.decide_final_offer('rfq','review',req,{'client_id':'tenant','role':'admin','user_id':'buyer'})
            assert exc.value.status_code==status
            fake.rpc.assert_not_called()
        else:
            result=await main.decide_final_offer('rfq','review',req,{'client_id':'tenant','role':'admin','user_id':'buyer'})
            assert result['queued'] is True
            assert fake.rpc.call_args.args[1]['p_counter']==counter
