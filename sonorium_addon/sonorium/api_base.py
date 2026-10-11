"""
The web server under ApiSonorium: a FastAPI app whose routes are listed as
Endpoints (path, HTTP method, handler), served by uvicorn.
"""
from dataclasses import dataclass
from typing import Callable, List, Optional, Union

import uvicorn
from fastapi import FastAPI

from sonorium.obs import logger


@dataclass
class Endpoint:
    """One route: `method_http` is the app's decorator for it (app.get, app.put, ...)."""
    method: Callable
    path: str
    tags: Optional[Union[str, List[str]]] = None
    method_http: Optional[Callable] = None

    def __post_init__(self):
        # Kept as a list, None included: the API docs list the routes as before
        self.tags = self.tags if isinstance(self.tags, list) else [self.tags]


class Base:
    """FastAPI app plus uvicorn server; subclasses list their routes in get_endpoints()."""
    TITLE = 'Base API'
    HOST = '0.0.0.0'
    PORT = 8080
    SWAGGER_PARAMS = dict(tryItOutEnabled=True)
    URL = None
    URL_DOCS = '/docs'

    def __init__(self):
        self.app = FastAPI(title=self.TITLE, swagger_ui_parameters=self.SWAGGER_PARAMS, docs_url=self.URL_DOCS)
        for endpoint in self.get_endpoints():
            self.add_endpoint(endpoint)

    def add_endpoint(self, endpoint: Endpoint):
        """Register a route; its handler's docstring is its summary and description in the API docs."""
        method_http = endpoint.method_http or self.app.post
        doc = (endpoint.method.__doc__ or '').strip() or None
        method_http(endpoint.path, tags=endpoint.tags, description=doc, summary=doc)(endpoint.method)

    def get_endpoints(self) -> List[Endpoint]:
        return []

    @property
    def url(self) -> str:
        return self.URL or f'http://{self.HOST}:{self.PORT}'

    @property
    def message(self) -> str:
        return f"Launching {self.TITLE} at {self.url}"

    @property
    def config(self) -> uvicorn.Config:
        return uvicorn.Config(self.app, host=self.HOST, port=self.PORT, access_log=False)

    @property
    def server(self) -> uvicorn.Server:
        return uvicorn.Server(self.config)

    @classmethod
    async def launch_async(cls, *args, **kwargs):
        """Create the API and serve it until shut down."""
        self = cls(*args, **kwargs)
        logger.debug(self.message)
        await self.server.serve()

    @classmethod
    def launch(cls, *args, **kwargs):
        import asyncio
        return asyncio.run(cls.launch_async(*args, **kwargs))
