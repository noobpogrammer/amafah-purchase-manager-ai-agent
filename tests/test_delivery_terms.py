import pytest
from commercial_policy import delivery_term, resolve_offers, validate_terms, delivery_authority_issue

@pytest.mark.parametrize('text,relation,days,unit',[
 ('Delivery after 4 days','after',4,'calendar_days'),('after4days','after',4,'calendar_days'),
 ('within 4 days','within',4,'calendar_days'),('Delivery in 4 days','exact',4,'calendar_days'),
 ('4 business days','exact',4,'business_days'),('Delivery: within 2 working days','within',2,'business_days')])
def test_delivery_semantics(text,relation,days,unit):
    assert delivery_term(text)=={'text':text,'relation':relation,'days':days,'unit':unit}

@pytest.mark.parametrize('text',['4-5 days','after payment','4 days if stock arrives','by next week',''])
def test_uncertain_delivery_is_not_invented(text):
    assert delivery_term(text) is None


def test_inherited_after_term_requires_exact_buyer_approval_without_rewording():
    rows=[{'id':'old','rfq_id':'r','supplier_id':'s','price':26,'quantity':10,'delivery_time':'Delivery after 4 days','created_at':'1'},
          {'id':'new','rfq_id':'r','supplier_id':'s','price':25.5,'quantity':10,'delivery_time':None,'created_at':'2'}]
    offer=resolve_offers(rows)[0]
    assert offer['resolved_delivery_days']==4
    assert offer['term_sources']['delivery_time']['quote_id']=='old'
    proposal={'price':25.5,'quantity':10,'delivery_days':4,'delivery_term':offer['delivery_term']}
    assert validate_terms({'quantity':10},offer,proposal)
    assert validate_terms({'quantity':10,'delivery_term_approved':True},offer,proposal) is None
    assert validate_terms({'quantity':10,'delivery_term_approved':True,'required_delivery_days':4},offer,proposal)
    changed={**proposal,'delivery_term':{**offer['delivery_term'],'relation':'within'}}
    assert validate_terms({'quantity':10,'delivery_term_approved':True},offer,changed)


def test_pending_review_refreshes_only_derived_delivery_fields():
    import buyer_quote_reviews as reviews
    from unittest.mock import patch, MagicMock
    offer=resolve_offers([{'id':'q','rfq_id':'r','supplier_id':'s','price':25.5,'quantity':10,'delivery_time':'Delivery after 4 days'}])[0]
    old={k:v for k,v in reviews.snapshot(offer).items() if k not in ('delivery_term','delivery_time')}
    old['resolved_delivery_days']=None
    pending={'id':'review','quote_id':'q','status':'pending','offer_snapshot':old}
    database=MagicMock()
    with patch.object(reviews,'list_reviews',return_value=[pending]),patch.object(reviews.db,'get_quotes_for_rfq',return_value=[offer]),patch.object(reviews.db,'supabase',database):
        reviews.refresh_pending_delivery_reviews('c','r')
        assert database.table.return_value.update.call_args.args[0]['offer_snapshot']['delivery_term']['relation']=='after'
    database.reset_mock()
    with patch.object(reviews,'list_reviews',return_value=[{**pending,'status':'authorized'}]),patch.object(reviews.db,'get_quotes_for_rfq',return_value=[offer]),patch.object(reviews.db,'supabase',database):
        reviews.refresh_pending_delivery_reviews('c','r')
        database.table.return_value.update.assert_not_called()
