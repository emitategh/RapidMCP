"""ResourceManager — registry for resources and resource templates."""

from __future__ import annotations

from collections.abc import Callable

from rapidmcp.resources.resource import RegisteredResource, RegisteredResourceTemplate
from rapidmcp.resources.uri_template import match_uri_template


class ResourceManager:
    """Owns the resource and resource-template registries."""

    def __init__(self) -> None:
        self._resources: dict[str, RegisteredResource] = {}
        self._resource_templates: dict[str, RegisteredResourceTemplate] = {}

    def resource(
        self,
        uri: str,
        *,
        description: str | None = None,
        mime_type: str = "text/plain",
    ) -> Callable:
        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            self._resources[uri] = RegisteredResource(
                uri=uri,
                name=fn.__name__,
                description=desc,
                mime_type=mime_type,
                handler=fn,
            )
            return fn

        return decorator

    def resource_template(
        self,
        uri_template: str,
        *,
        description: str | None = None,
        mime_type: str = "text/plain",
    ) -> Callable:
        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            self._resource_templates[uri_template] = RegisteredResourceTemplate(
                uri_template=uri_template,
                name=fn.__name__,
                description=desc,
                mime_type=mime_type,
                handler=fn,
            )
            return fn

        return decorator

    def resolve(
        self, uri: str
    ) -> tuple[RegisteredResource | RegisteredResourceTemplate, dict[str, str]] | None:
        """The resource or template that serves *uri*, with its template parameters."""
        resource = self._resources.get(uri)
        if resource:
            return resource, {}
        for template in self._resource_templates.values():
            params = match_uri_template(uri, template.uri_template)
            if params is not None:
                return template, params
        return None

    def list_registered_resources(self) -> list[RegisteredResource]:
        return list(self._resources.values())

    def list_registered_resource_templates(self) -> list[RegisteredResourceTemplate]:
        return list(self._resource_templates.values())
