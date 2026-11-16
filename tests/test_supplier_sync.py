from types import SimpleNamespace
from unittest.mock import patch,MagicMock
import pytest
from fastapi import HTTPException
import main


def test_sync_timeout_covers_gateway_restart_and_requires_confirmation():
    response=MagicMock(ok=True);response.json.return_value={'ok':True}
    with patch.object(main,'OPENCLAW_BASE_URL','https://gateway.invalid'),patch.object(main,'OPENCLAW_TOOL_API_KEY','test'),patch.object(main.requests,'post',return_value=response) as post:
        assert main.sync_openclaw_allowlist('+971500000000','add')['ok'] is True
    assert post.call_args.kwargs['timeout']==(5,90)


def test_sync_does_not_report_success_on_negative_receipt():
    response=MagicMock(ok=True);response.json.return_value={'ok':False}
    with patch.object(main,'OPENCLAW_BASE_URL','https://gateway.invalid'),patch.object(main,'OPENCLAW_TOOL_API_KEY','test'),patch.object(main.requests,'post',return_value=response):
        with pytest.raises(HTTPException):main.sync_openclaw_allowlist('+971500000000','add')


@pytest.mark.asyncio
async def test_contact_not_reported_saved_until_gateway_confirms():
    supplier={'id':'supplier'}
    async def worker(fn,*args):
        assert fn is main.sync_openclaw_allowlist
        assert args==('+971500000000','add')
        return {'ok':True}
    with patch.object(main.db,'create_supplier',return_value=[supplier]),patch.object(main,'run_in_threadpool',side_effect=worker) as background,patch.object(main.db,'soft_delete_supplier') as rollback:
        result=await main.create_supplier_endpoint(main.SupplierMutationRequest(name='Test',phone_number='+971500000000'),{'client_id':'tenant'})
    assert result['status']=='created';background.assert_awaited_once();rollback.assert_not_called()
