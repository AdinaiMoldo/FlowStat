"""A router in which every route names its permission (or says it is public).

The permission is checked by a dependency and written into the OpenAPI document as
``x-permission``, which is what the test that walks all routes reads.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends

from flowstat.api.deps import require
from flowstat.api.errors import ERROR_RESPONSES
from flowstat.service.permissions import PUBLIC


class SecuredRouter(APIRouter):
    def secured(
        self, method: str, path: str, permission: str, **options: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        extra = {"x-permission": permission} | options.pop("openapi_extra", {})
        dependencies = [] if permission == PUBLIC else [Depends(require(permission))]
        responses = options.pop("responses", {})
        return self.api_route(
            path,
            methods=[method],
            dependencies=dependencies,
            openapi_extra=extra,
            responses=(ERROR_RESPONSES if permission != PUBLIC else {}) | responses,
            **options,
        )
