"""
HTTP client for calls to Home Assistant's REST API.

A plain httpx client with a 10 second timeout that retries a failed request:
connection errors, timeouts and 429/502/503/504 replies are tried again (up to
10 times, waiting longer each time). GET and the other safe methods only.
"""
from functools import cached_property

import httpx
from httpx_retries import Retry, RetryTransport


class Client(httpx.Client):
    """httpx client with Sonorium's timeout and retries."""

    TIMEOUT = 10

    def __init__(self, *args, **kwargs):
        super().__init__(*args, transport=self.transport, timeout=self.TIMEOUT, **kwargs)

    @cached_property
    def transport(self) -> RetryTransport:
        return RetryTransport(retry=self.retry)

    @cached_property
    def retry(self) -> Retry:
        return Retry(allowed_methods=Retry.RETRYABLE_METHODS, backoff_factor=1.0)
