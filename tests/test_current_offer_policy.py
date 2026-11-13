"""No live database or supplier messages. Cover missing terms and authority boundaries."""
import os
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import pytest
from commercial_policy import resolve_offers, price_policy, price_anomaly, validate_terms, apply_constraints


def history(**changes):
    old = dict(id='old', rfq_id='r', supplier_id='s', variant_label='50W', price=12,
               quantity=10, delivery_time='2 days', quality_notes='Brand A', is_available=True, created_at='2026-11-13')
    new = dict(old, id='new', price=11, quantity=None, delivery_time=None, quality_notes=None, created_at='2026-11-14')
    new.update(changes)
    return [new, old]


def test_price_only_revision_keeps_provenance_and_quantity():
    q = resolve_offers(history())[0]
    assert (q['price'], q['quantity'], q['resolved_delivery_days'], q['quality_notes']) == (11, 10, 2, 'Brand A')
    assert q['term_sources']['quantity']['quote_id'] == 'old'
    assert q['term_sources']['price']['quote_id'] == 'new'


def test_explicit_change_wins_and_unknown_is_not_buyer_request():
    q = resolve_offers(history(delivery_time='7 days', quantity=100))[0]
    assert (q['quantity'], q['resolved_delivery_days']) == (100, 7)
    q = resolve_offers(history(delivery_time='to be confirmed')[:1])[0]
    assert q['quantity'] is None and q['resolved_delivery_days'] is None
    assert validate_terms({'quantity':10, 'required_delivery_days':2}, q, {'quantity':10,'delivery_days':2})


def test_variants_and_suppliers_never_cross_inherit():
    rows = history(variant_label=None)
    assert resolve_offers(rows)[0]['quantity'] == 10  # Sole known variant.
    rows.append(dict(rows[1], id='other', variant_label='30W', quantity=100))
    new = next(q for q in resolve_offers(rows) if q['id']=='new')
    assert new['quantity'] is None
    rows = history();rows[1]['supplier_id']='someone-else'
    assert next(q for q in resolve_offers(rows) if q['id']=='new')['quantity'] is None


def test_withdrawal_stops_old_terms_reappearing():
    rows = history();rows.insert(1,dict(rows[0],id='withdrawn',is_available=False,created_at='2026-11-13T23:00'))
    assert resolve_offers(rows)[0]['quantity'] is None


@pytest.mark.parametrize('rfq,target,cap', [
    ({'last_quote':12},12,17),
    ({'last_quote':12,'acceptable_price_min':10,'acceptable_price_max':14},10,14),
    ({'last_quote':12,'acceptable_price_max':14},12,14),
    ({'acceptable_price_min':12},12,None), ({},None,None),
    ({'last_quote':float('nan')},None,None),
])
def test_price_precedence(rfq,target,cap):
    p=price_policy(rfq);assert (p['preferred_target'],p['acceptable_max'])==(target,cap)


def test_authorized_tradeoff_accepted_but_invented_terms_rejected():
    rfq=apply_constraints({'quantity':10,'required_delivery_days':2},[
        {'dimension':'quantity','status':'authorized','constraints':{'min':10,'max':20}},
        {'dimension':'delivery','status':'authorized','constraints':{'max_days':4}}])
    offer={'quantity':15,'resolved_delivery_days':3}
    assert validate_terms(rfq,offer,{'quantity':15,'delivery_days':3}) is None
    assert validate_terms(rfq,offer,{'quantity':10,'delivery_days':2})
    assert validate_terms(rfq,{'quantity':5,'resolved_delivery_days':3},{'quantity':5,'delivery_days':3})
    assert price_anomaly({'last_quote':12},{'price':120})
    assert not price_anomaly({'last_quote':12},{'price':18})


class Query:
    def __init__(self, tables, name): self.tables=tables;self.name=name;self.filters=[];self.payload=None
    def select(self,*a,**k): return self
    def eq(self,k,v): self.filters.append(lambda row: row.get(k)==v);return self
    def in_(self,k,v): self.filters.append(lambda row: row.get(k) in v);return self
    def contains(self,k,v): self.filters.append(lambda row: all((row.get(k) or {}).get(x)==y for x,y in v.items()));return self
    def limit(self,*a): return self
    def order(self,*a,**k): return self
    def insert(self,p): self.payload=p;return self
    def execute(self):
        if self.payload is not None:
            row=dict(self.payload,id='approval');self.tables.setdefault(self.name,[]).append(row)
            return SimpleNamespace(data=[row])
        return SimpleNamespace(data=[r for r in self.tables.get(self.name,[]) if all(f(r) for f in self.filters)])


@pytest.fixture
def review_env():
    import openclaw_bridge as b
    rfq={'id':'r','client_id':'c','status':'active','last_quote':12,'quantity':10,'required_delivery_days':2}
    offer=resolve_offers(history())[0]
    tables={'quotes':[offer]}
    fake=MagicMock();fake.table.side_effect=lambda name: Query(tables,name)
    with patch.object(b,'_require_openclaw_key'),patch.object(b,'_get_supplier_for_client',return_value={'id':'s'}),patch.object(b,'_get_rfq_and_verify_link',return_value=rfq),patch.object(b,'_require_current_bundle_rfq'),patch.object(b.db,'get_active_negotiation_session',return_value=None),patch.object(b.db,'get_rfq_negotiation_constraints',return_value=[]),patch.object(b.db,'get_quotes_for_rfq',return_value=[offer]),patch.object(b.db,'is_rfq_open',return_value=True),patch.object(b.db,'supabase',fake),patch.object(b.db,'flag_for_human_review') as flag:
        yield b,rfq,offer,tables,fake,flag


def review(env,action,price=11,**extra):
    b,*_=env
    req=b.CommercialActionReviewRequest(client_id='c',supplier_id='s',rfq_id='r',quote_id='new',action_type=action,proposal={'price':price,'quantity':10,'delivery_days':2},**extra)
    return b.review_commercial_action(req)


def test_review_accepts_inherited_terms_and_last_only_ceiling(review_env):
    assert review(review_env,'accept')['outcome']=='APPROVE'
    assert review_env[3]['commercial_action_intents'][0]['guard_snapshot']['current_offer']['term_sources']['quantity']['quote_id']=='old'


def test_review_refuses_invented_delivery(review_env):
    review_env[2]['resolved_delivery_days']=7
    assert review(review_env,'accept')['outcome']!='APPROVE'


def test_huge_offer_flags_without_counter(review_env):
    review_env[2]['price']=120
    assert review(review_env,'counter',price=12)['outcome']=='ESCALATE'
    review_env[5].assert_called_once()


def test_only_one_sent_extra_savings_ask(review_env):
    assert review(review_env,'counter',price=10)['outcome']=='APPROVE'
    review_env[3]['commercial_action_intents'][0]['consumed_at']='sent'
    assert review(review_env,'counter',price=9)['outcome']=='REJECT'


def test_second_failed_proposal_escalates(review_env):
    assert review(review_env,'counter',price=30,attempt_number=2)['outcome']=='ESCALATE'


def test_acceptance_uses_one_atomic_rpc(review_env):
    b,rfq,offer,tables,fake,flag=review_env
    fake.rpc.return_value.execute.return_value.data={'status':'accepted'}
    result=b.accept_negotiated_quote(b.AcceptNegotiatedQuoteRequest(client_id='c',supplier_id='s',rfq_id='r',quote_id='new',approval_id='approval',price=11,quantity=10))
    assert result['accepted']
    assert fake.rpc.call_args.args[0]=='accept_resolved_quote'


def test_verified_anomaly_can_resume_negotiation_but_not_raise_cap(review_env):
    review_env[2]['price']=120
    review_env[3]['flagged_for_review']=[{'client_id':'c','supplier_id':'s','rfq_id':'r',
        'category':'price_anomaly','status':'resolved','metadata':{'price_verified':True,'quote_id':'new','price':120}}]
    assert review(review_env,'counter',price=12)['outcome']=='APPROVE'
    assert review(review_env,'accept',price=120)['outcome']=='ESCALATE'
