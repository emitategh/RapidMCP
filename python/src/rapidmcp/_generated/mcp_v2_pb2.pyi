from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CacheScope(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CACHE_SCOPE_PRIVATE: _ClassVar[CacheScope]
    CACHE_SCOPE_PUBLIC: _ClassVar[CacheScope]
CACHE_SCOPE_PRIVATE: CacheScope
CACHE_SCOPE_PUBLIC: CacheScope

class Implementation(_message.Message):
    __slots__ = ("name", "version")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    name: str
    version: str
    def __init__(self, name: _Optional[str] = ..., version: _Optional[str] = ...) -> None: ...

class ElicitationCapability(_message.Message):
    __slots__ = ("form", "url")
    FORM_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    form: bool
    url: bool
    def __init__(self, form: bool = ..., url: bool = ...) -> None: ...

class ClientCapabilities(_message.Message):
    __slots__ = ("elicitation", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ELICITATION_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    elicitation: ElicitationCapability
    extensions: _containers.ScalarMap[str, str]
    def __init__(self, elicitation: _Optional[_Union[ElicitationCapability, _Mapping]] = ..., extensions: _Optional[_Mapping[str, str]] = ...) -> None: ...

class RequestMeta(_message.Message):
    __slots__ = ("protocol_version", "client_capabilities", "client_info", "log_level", "progress_token")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    CLIENT_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    CLIENT_INFO_FIELD_NUMBER: _ClassVar[int]
    LOG_LEVEL_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    protocol_version: str
    client_capabilities: ClientCapabilities
    client_info: Implementation
    log_level: str
    progress_token: str
    def __init__(self, protocol_version: _Optional[str] = ..., client_capabilities: _Optional[_Union[ClientCapabilities, _Mapping]] = ..., client_info: _Optional[_Union[Implementation, _Mapping]] = ..., log_level: _Optional[str] = ..., progress_token: _Optional[str] = ...) -> None: ...

class ResultMeta(_message.Message):
    __slots__ = ("server_info",)
    SERVER_INFO_FIELD_NUMBER: _ClassVar[int]
    server_info: Implementation
    def __init__(self, server_info: _Optional[_Union[Implementation, _Mapping]] = ...) -> None: ...

class CacheHint(_message.Message):
    __slots__ = ("ttl_ms", "scope")
    TTL_MS_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    ttl_ms: int
    scope: CacheScope
    def __init__(self, ttl_ms: _Optional[int] = ..., scope: _Optional[_Union[CacheScope, str]] = ...) -> None: ...

class ErrorData(_message.Message):
    __slots__ = ("supported_versions", "requested_version", "required_capabilities")
    SUPPORTED_VERSIONS_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    supported_versions: _containers.RepeatedScalarFieldContainer[str]
    requested_version: str
    required_capabilities: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, supported_versions: _Optional[_Iterable[str]] = ..., requested_version: _Optional[str] = ..., required_capabilities: _Optional[_Iterable[str]] = ...) -> None: ...

class ToolsCapability(_message.Message):
    __slots__ = ("list_changed",)
    LIST_CHANGED_FIELD_NUMBER: _ClassVar[int]
    list_changed: bool
    def __init__(self, list_changed: bool = ...) -> None: ...

class ResourcesCapability(_message.Message):
    __slots__ = ("list_changed", "subscribe")
    LIST_CHANGED_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBE_FIELD_NUMBER: _ClassVar[int]
    list_changed: bool
    subscribe: bool
    def __init__(self, list_changed: bool = ..., subscribe: bool = ...) -> None: ...

class PromptsCapability(_message.Message):
    __slots__ = ("list_changed",)
    LIST_CHANGED_FIELD_NUMBER: _ClassVar[int]
    list_changed: bool
    def __init__(self, list_changed: bool = ...) -> None: ...

class ServerCapabilities(_message.Message):
    __slots__ = ("tools", "resources", "prompts", "logging", "extensions")
    class ExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    PROMPTS_FIELD_NUMBER: _ClassVar[int]
    LOGGING_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    tools: ToolsCapability
    resources: ResourcesCapability
    prompts: PromptsCapability
    logging: bool
    extensions: _containers.ScalarMap[str, str]
    def __init__(self, tools: _Optional[_Union[ToolsCapability, _Mapping]] = ..., resources: _Optional[_Union[ResourcesCapability, _Mapping]] = ..., prompts: _Optional[_Union[PromptsCapability, _Mapping]] = ..., logging: bool = ..., extensions: _Optional[_Mapping[str, str]] = ...) -> None: ...

class DiscoverRequest(_message.Message):
    __slots__ = ("meta",)
    META_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ...) -> None: ...

class DiscoverResult(_message.Message):
    __slots__ = ("meta", "supported_versions", "capabilities", "instructions", "cache")
    META_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_VERSIONS_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    CACHE_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    supported_versions: _containers.RepeatedScalarFieldContainer[str]
    capabilities: ServerCapabilities
    instructions: str
    cache: CacheHint
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., supported_versions: _Optional[_Iterable[str]] = ..., capabilities: _Optional[_Union[ServerCapabilities, _Mapping]] = ..., instructions: _Optional[str] = ..., cache: _Optional[_Union[CacheHint, _Mapping]] = ...) -> None: ...

class ToolAnnotations(_message.Message):
    __slots__ = ("title", "read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_HINT_FIELD_NUMBER: _ClassVar[int]
    DESTRUCTIVE_HINT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENT_HINT_FIELD_NUMBER: _ClassVar[int]
    OPEN_WORLD_HINT_FIELD_NUMBER: _ClassVar[int]
    title: str
    read_only_hint: bool
    destructive_hint: bool
    idempotent_hint: bool
    open_world_hint: bool
    def __init__(self, title: _Optional[str] = ..., read_only_hint: bool = ..., destructive_hint: bool = ..., idempotent_hint: bool = ..., open_world_hint: bool = ...) -> None: ...

class Tool(_message.Message):
    __slots__ = ("name", "description", "input_schema", "output_schema", "annotations")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    INPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    input_schema: str
    output_schema: str
    annotations: ToolAnnotations
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., input_schema: _Optional[str] = ..., output_schema: _Optional[str] = ..., annotations: _Optional[_Union[ToolAnnotations, _Mapping]] = ...) -> None: ...

class ListToolsRequest(_message.Message):
    __slots__ = ("meta", "cursor")
    META_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    cursor: str
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ..., cursor: _Optional[str] = ...) -> None: ...

class ListToolsResult(_message.Message):
    __slots__ = ("meta", "tools", "next_cursor", "cache")
    META_FIELD_NUMBER: _ClassVar[int]
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    CACHE_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    tools: _containers.RepeatedCompositeFieldContainer[Tool]
    next_cursor: str
    cache: CacheHint
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., tools: _Optional[_Iterable[_Union[Tool, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., cache: _Optional[_Union[CacheHint, _Mapping]] = ...) -> None: ...

class Resource(_message.Message):
    __slots__ = ("uri", "name", "description", "mime_type")
    URI_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    uri: str
    name: str
    description: str
    mime_type: str
    def __init__(self, uri: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., mime_type: _Optional[str] = ...) -> None: ...

class ListResourcesRequest(_message.Message):
    __slots__ = ("meta", "cursor")
    META_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    cursor: str
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ..., cursor: _Optional[str] = ...) -> None: ...

class ListResourcesResult(_message.Message):
    __slots__ = ("meta", "resources", "next_cursor", "cache")
    META_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    CACHE_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    resources: _containers.RepeatedCompositeFieldContainer[Resource]
    next_cursor: str
    cache: CacheHint
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., resources: _Optional[_Iterable[_Union[Resource, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., cache: _Optional[_Union[CacheHint, _Mapping]] = ...) -> None: ...

class ResourceTemplate(_message.Message):
    __slots__ = ("uri_template", "name", "description", "mime_type")
    URI_TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    uri_template: str
    name: str
    description: str
    mime_type: str
    def __init__(self, uri_template: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., mime_type: _Optional[str] = ...) -> None: ...

class ListResourceTemplatesRequest(_message.Message):
    __slots__ = ("meta", "cursor")
    META_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    cursor: str
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ..., cursor: _Optional[str] = ...) -> None: ...

class ListResourceTemplatesResult(_message.Message):
    __slots__ = ("meta", "templates", "next_cursor", "cache")
    META_FIELD_NUMBER: _ClassVar[int]
    TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    CACHE_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    templates: _containers.RepeatedCompositeFieldContainer[ResourceTemplate]
    next_cursor: str
    cache: CacheHint
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., templates: _Optional[_Iterable[_Union[ResourceTemplate, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., cache: _Optional[_Union[CacheHint, _Mapping]] = ...) -> None: ...

class PromptArgument(_message.Message):
    __slots__ = ("name", "description", "required")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    required: bool
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., required: bool = ...) -> None: ...

class Prompt(_message.Message):
    __slots__ = ("name", "description", "arguments")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    arguments: _containers.RepeatedCompositeFieldContainer[PromptArgument]
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., arguments: _Optional[_Iterable[_Union[PromptArgument, _Mapping]]] = ...) -> None: ...

class ListPromptsRequest(_message.Message):
    __slots__ = ("meta", "cursor")
    META_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    cursor: str
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ..., cursor: _Optional[str] = ...) -> None: ...

class ListPromptsResult(_message.Message):
    __slots__ = ("meta", "prompts", "next_cursor", "cache")
    META_FIELD_NUMBER: _ClassVar[int]
    PROMPTS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    CACHE_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    prompts: _containers.RepeatedCompositeFieldContainer[Prompt]
    next_cursor: str
    cache: CacheHint
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., prompts: _Optional[_Iterable[_Union[Prompt, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., cache: _Optional[_Union[CacheHint, _Mapping]] = ...) -> None: ...

class CompletionRef(_message.Message):
    __slots__ = ("type", "name")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    type: str
    name: str
    def __init__(self, type: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class CompletionArg(_message.Message):
    __slots__ = ("name", "value")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    name: str
    value: str
    def __init__(self, name: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class CompleteRequest(_message.Message):
    __slots__ = ("meta", "ref", "argument")
    META_FIELD_NUMBER: _ClassVar[int]
    REF_FIELD_NUMBER: _ClassVar[int]
    ARGUMENT_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    ref: CompletionRef
    argument: CompletionArg
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ..., ref: _Optional[_Union[CompletionRef, _Mapping]] = ..., argument: _Optional[_Union[CompletionArg, _Mapping]] = ...) -> None: ...

class CompleteResult(_message.Message):
    __slots__ = ("meta", "values", "has_more", "total")
    META_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    HAS_MORE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    values: _containers.RepeatedScalarFieldContainer[str]
    has_more: bool
    total: int
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., values: _Optional[_Iterable[str]] = ..., has_more: bool = ..., total: _Optional[int] = ...) -> None: ...

class ContentItem(_message.Message):
    __slots__ = ("type", "text", "data", "mime_type", "uri")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    URI_FIELD_NUMBER: _ClassVar[int]
    type: str
    text: str
    data: bytes
    mime_type: str
    uri: str
    def __init__(self, type: _Optional[str] = ..., text: _Optional[str] = ..., data: _Optional[bytes] = ..., mime_type: _Optional[str] = ..., uri: _Optional[str] = ...) -> None: ...

class Progress(_message.Message):
    __slots__ = ("token", "progress", "total", "message")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    token: str
    progress: float
    total: float
    message: str
    def __init__(self, token: _Optional[str] = ..., progress: _Optional[float] = ..., total: _Optional[float] = ..., message: _Optional[str] = ...) -> None: ...

class LogMessage(_message.Message):
    __slots__ = ("level", "logger", "data")
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    LOGGER_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    level: str
    logger: str
    data: str
    def __init__(self, level: _Optional[str] = ..., logger: _Optional[str] = ..., data: _Optional[str] = ...) -> None: ...

class ElicitForm(_message.Message):
    __slots__ = ("requested_schema",)
    REQUESTED_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    requested_schema: str
    def __init__(self, requested_schema: _Optional[str] = ...) -> None: ...

class ElicitUrl(_message.Message):
    __slots__ = ("url",)
    URL_FIELD_NUMBER: _ClassVar[int]
    url: str
    def __init__(self, url: _Optional[str] = ...) -> None: ...

class ElicitRequest(_message.Message):
    __slots__ = ("message", "form", "url")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    FORM_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    message: str
    form: ElicitForm
    url: ElicitUrl
    def __init__(self, message: _Optional[str] = ..., form: _Optional[_Union[ElicitForm, _Mapping]] = ..., url: _Optional[_Union[ElicitUrl, _Mapping]] = ...) -> None: ...

class ElicitResult(_message.Message):
    __slots__ = ("action", "content")
    ACTION_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    action: str
    content: str
    def __init__(self, action: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class InputRequest(_message.Message):
    __slots__ = ("elicit",)
    ELICIT_FIELD_NUMBER: _ClassVar[int]
    elicit: ElicitRequest
    def __init__(self, elicit: _Optional[_Union[ElicitRequest, _Mapping]] = ...) -> None: ...

class InputResponse(_message.Message):
    __slots__ = ("elicit",)
    ELICIT_FIELD_NUMBER: _ClassVar[int]
    elicit: ElicitResult
    def __init__(self, elicit: _Optional[_Union[ElicitResult, _Mapping]] = ...) -> None: ...

class InputRequired(_message.Message):
    __slots__ = ("input_requests", "request_state")
    class InputRequestsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: InputRequest
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[InputRequest, _Mapping]] = ...) -> None: ...
    INPUT_REQUESTS_FIELD_NUMBER: _ClassVar[int]
    REQUEST_STATE_FIELD_NUMBER: _ClassVar[int]
    input_requests: _containers.MessageMap[str, InputRequest]
    request_state: bytes
    def __init__(self, input_requests: _Optional[_Mapping[str, InputRequest]] = ..., request_state: _Optional[bytes] = ...) -> None: ...

class CallToolRequest(_message.Message):
    __slots__ = ("meta", "name", "arguments", "input_responses", "request_state")
    class InputResponsesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: InputResponse
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[InputResponse, _Mapping]] = ...) -> None: ...
    META_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    INPUT_RESPONSES_FIELD_NUMBER: _ClassVar[int]
    REQUEST_STATE_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    name: str
    arguments: str
    input_responses: _containers.MessageMap[str, InputResponse]
    request_state: bytes
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ..., name: _Optional[str] = ..., arguments: _Optional[str] = ..., input_responses: _Optional[_Mapping[str, InputResponse]] = ..., request_state: _Optional[bytes] = ...) -> None: ...

class CallToolResult(_message.Message):
    __slots__ = ("meta", "content", "is_error", "structured_content")
    META_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    IS_ERROR_FIELD_NUMBER: _ClassVar[int]
    STRUCTURED_CONTENT_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    content: _containers.RepeatedCompositeFieldContainer[ContentItem]
    is_error: bool
    structured_content: str
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., content: _Optional[_Iterable[_Union[ContentItem, _Mapping]]] = ..., is_error: bool = ..., structured_content: _Optional[str] = ...) -> None: ...

class CallToolEvent(_message.Message):
    __slots__ = ("progress", "log", "complete", "input_required")
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    LOG_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    INPUT_REQUIRED_FIELD_NUMBER: _ClassVar[int]
    progress: Progress
    log: LogMessage
    complete: CallToolResult
    input_required: InputRequired
    def __init__(self, progress: _Optional[_Union[Progress, _Mapping]] = ..., log: _Optional[_Union[LogMessage, _Mapping]] = ..., complete: _Optional[_Union[CallToolResult, _Mapping]] = ..., input_required: _Optional[_Union[InputRequired, _Mapping]] = ...) -> None: ...

class ReadResourceRequest(_message.Message):
    __slots__ = ("meta", "uri", "input_responses", "request_state")
    class InputResponsesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: InputResponse
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[InputResponse, _Mapping]] = ...) -> None: ...
    META_FIELD_NUMBER: _ClassVar[int]
    URI_FIELD_NUMBER: _ClassVar[int]
    INPUT_RESPONSES_FIELD_NUMBER: _ClassVar[int]
    REQUEST_STATE_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    uri: str
    input_responses: _containers.MessageMap[str, InputResponse]
    request_state: bytes
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ..., uri: _Optional[str] = ..., input_responses: _Optional[_Mapping[str, InputResponse]] = ..., request_state: _Optional[bytes] = ...) -> None: ...

class ReadResourceResult(_message.Message):
    __slots__ = ("meta", "content", "cache")
    META_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    CACHE_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    content: _containers.RepeatedCompositeFieldContainer[ContentItem]
    cache: CacheHint
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., content: _Optional[_Iterable[_Union[ContentItem, _Mapping]]] = ..., cache: _Optional[_Union[CacheHint, _Mapping]] = ...) -> None: ...

class ReadResourceEvent(_message.Message):
    __slots__ = ("progress", "log", "complete", "input_required")
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    LOG_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    INPUT_REQUIRED_FIELD_NUMBER: _ClassVar[int]
    progress: Progress
    log: LogMessage
    complete: ReadResourceResult
    input_required: InputRequired
    def __init__(self, progress: _Optional[_Union[Progress, _Mapping]] = ..., log: _Optional[_Union[LogMessage, _Mapping]] = ..., complete: _Optional[_Union[ReadResourceResult, _Mapping]] = ..., input_required: _Optional[_Union[InputRequired, _Mapping]] = ...) -> None: ...

class GetPromptRequest(_message.Message):
    __slots__ = ("meta", "name", "arguments", "input_responses", "request_state")
    class ArgumentsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class InputResponsesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: InputResponse
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[InputResponse, _Mapping]] = ...) -> None: ...
    META_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    INPUT_RESPONSES_FIELD_NUMBER: _ClassVar[int]
    REQUEST_STATE_FIELD_NUMBER: _ClassVar[int]
    meta: RequestMeta
    name: str
    arguments: _containers.ScalarMap[str, str]
    input_responses: _containers.MessageMap[str, InputResponse]
    request_state: bytes
    def __init__(self, meta: _Optional[_Union[RequestMeta, _Mapping]] = ..., name: _Optional[str] = ..., arguments: _Optional[_Mapping[str, str]] = ..., input_responses: _Optional[_Mapping[str, InputResponse]] = ..., request_state: _Optional[bytes] = ...) -> None: ...

class PromptMessage(_message.Message):
    __slots__ = ("role", "content")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    role: str
    content: ContentItem
    def __init__(self, role: _Optional[str] = ..., content: _Optional[_Union[ContentItem, _Mapping]] = ...) -> None: ...

class GetPromptResult(_message.Message):
    __slots__ = ("meta", "messages")
    META_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    meta: ResultMeta
    messages: _containers.RepeatedCompositeFieldContainer[PromptMessage]
    def __init__(self, meta: _Optional[_Union[ResultMeta, _Mapping]] = ..., messages: _Optional[_Iterable[_Union[PromptMessage, _Mapping]]] = ...) -> None: ...

class GetPromptEvent(_message.Message):
    __slots__ = ("progress", "log", "complete", "input_required")
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    LOG_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    INPUT_REQUIRED_FIELD_NUMBER: _ClassVar[int]
    progress: Progress
    log: LogMessage
    complete: GetPromptResult
    input_required: InputRequired
    def __init__(self, progress: _Optional[_Union[Progress, _Mapping]] = ..., log: _Optional[_Union[LogMessage, _Mapping]] = ..., complete: _Optional[_Union[GetPromptResult, _Mapping]] = ..., input_required: _Optional[_Union[InputRequired, _Mapping]] = ...) -> None: ...
