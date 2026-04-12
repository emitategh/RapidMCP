import asyncio

import pytest

from fastermcp.session import PendingRequests


@pytest.mark.asyncio
async def test_pending_request_resolve():
    pending = PendingRequests()
    future = pending.create(request_id=1)
    assert not future.done()
    pending.resolve(request_id=1, result={"text": "hello"})
    result = await asyncio.wait_for(future, timeout=1.0)
    assert result == {"text": "hello"}


@pytest.mark.asyncio
async def test_pending_request_reject():
    pending = PendingRequests()
    future = pending.create(request_id=2)
    pending.reject(request_id=2, error=Exception("not found"))
    with pytest.raises(Exception, match="not found"):
        await asyncio.wait_for(future, timeout=1.0)


@pytest.mark.asyncio
async def test_pending_cancel_all():
    pending = PendingRequests()
    f1 = pending.create(request_id=1)
    f2 = pending.create(request_id=2)
    pending.cancel_all()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(f1, timeout=1.0)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(f2, timeout=1.0)


def test_next_request_id():
    pending = PendingRequests()
    assert pending.next_id() == 1
    assert pending.next_id() == 2
    assert pending.next_id() == 3
