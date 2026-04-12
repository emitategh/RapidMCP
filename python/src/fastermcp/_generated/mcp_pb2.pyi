from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ClientEnvelope(_message.Message):
    __slots__ = ("request_id", "initialize", "initialized", "list_tools", "call_tool", "list_resources", "read_resource", "subscribe_res", "list_resource_templates", "list_prompts", "get_prompt", "complete", "sampling_reply", "elicitation_reply", "roots_reply", "ping", "cancel", "client_notification")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    INITIALIZE_FIELD_NUMBER: _ClassVar[int]
    INITIALIZED_FIELD_NUMBER: _ClassVar[int]
    LIST_TOOLS_FIELD_NUMBER: _ClassVar[int]
    CALL_TOOL_FIELD_NUMBER: _ClassVar[int]
    LIST_RESOURCES_FIELD_NUMBER: _ClassVar[int]
    READ_RESOURCE_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBE_RES_FIELD_NUMBER: _ClassVar[int]
    LIST_RESOURCE_TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    LIST_PROMPTS_FIELD_NUMBER: _ClassVar[int]
    GET_PROMPT_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    SAMPLING_REPLY_FIELD_NUMBER: _ClassVar[int]
    ELICITATION_REPLY_FIELD_NUMBER: _ClassVar[int]
    ROOTS_REPLY_FIELD_NUMBER: _ClassVar[int]
    PING_FIELD_NUMBER: _ClassVar[int]
    CANCEL_FIELD_NUMBER: _ClassVar[int]
    CLIENT_NOTIFICATION_FIELD_NUMBER: _ClassVar[int]
    request_id: int
    initialize: InitializeRequest
    initialized: InitializedAck
    list_tools: ListToolsRequest
    call_tool: CallToolRequest
    list_resources: ListResourcesRequest
    read_resource: ReadResourceRequest
    subscribe_res: SubscribeResourceReq
    list_resource_templates: ListResourceTemplatesRequest
    list_prompts: ListPromptsRequest
    get_prompt: GetPromptRequest
    complete: CompleteRequest
    sampling_reply: SamplingResponse
    elicitation_reply: ElicitationResponse
    roots_reply: ListRootsResponse
    ping: PingRequest
    cancel: CancelRequest
    client_notification: ClientNotification
    def __init__(self, request_id: _Optional[int] = ..., initialize: _Optional[_Union[InitializeRequest, _Mapping]] = ..., initialized: _Optional[_Union[InitializedAck, _Mapping]] = ..., list_tools: _Optional[_Union[ListToolsRequest, _Mapping]] = ..., call_tool: _Optional[_Union[CallToolRequest, _Mapping]] = ..., list_resources: _Optional[_Union[ListResourcesRequest, _Mapping]] = ..., read_resource: _Optional[_Union[ReadResourceRequest, _Mapping]] = ..., subscribe_res: _Optional[_Union[SubscribeResourceReq, _Mapping]] = ..., list_resource_templates: _Optional[_Union[ListResourceTemplatesRequest, _Mapping]] = ..., list_prompts: _Optional[_Union[ListPromptsRequest, _Mapping]] = ..., get_prompt: _Optional[_Union[GetPromptRequest, _Mapping]] = ..., complete: _Optional[_Union[CompleteRequest, _Mapping]] = ..., sampling_reply: _Optional[_Union[SamplingResponse, _Mapping]] = ..., elicitation_reply: _Optional[_Union[ElicitationResponse, _Mapping]] = ..., roots_reply: _Optional[_Union[ListRootsResponse, _Mapping]] = ..., ping: _Optional[_Union[PingRequest, _Mapping]] = ..., cancel: _Optional[_Union[CancelRequest, _Mapping]] = ..., client_notification: _Optional[_Union[ClientNotification, _Mapping]] = ...) -> None: ...

class ServerEnvelope(_message.Message):
    __slots__ = ("request_id", "initialize", "list_tools", "call_tool", "list_resources", "read_resource", "list_resource_templates", "list_prompts", "get_prompt", "complete", "sampling", "elicitation", "roots_request", "notification", "pong", "error")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    INITIALIZE_FIELD_NUMBER: _ClassVar[int]
    LIST_TOOLS_FIELD_NUMBER: _ClassVar[int]
    CALL_TOOL_FIELD_NUMBER: _ClassVar[int]
    LIST_RESOURCES_FIELD_NUMBER: _ClassVar[int]
    READ_RESOURCE_FIELD_NUMBER: _ClassVar[int]
    LIST_RESOURCE_TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    LIST_PROMPTS_FIELD_NUMBER: _ClassVar[int]
    GET_PROMPT_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    SAMPLING_FIELD_NUMBER: _ClassVar[int]
    ELICITATION_FIELD_NUMBER: _ClassVar[int]
    ROOTS_REQUEST_FIELD_NUMBER: _ClassVar[int]
    NOTIFICATION_FIELD_NUMBER: _ClassVar[int]
    PONG_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    request_id: int
    initialize: InitializeResponse
    list_tools: ListToolsResponse
    call_tool: CallToolResponse
    list_resources: ListResourcesResponse
    read_resource: ReadResourceResponse
    list_resource_templates: ListResourceTemplatesResponse
    list_prompts: ListPromptsResponse
    get_prompt: GetPromptResponse
    complete: CompleteResponse
    sampling: SamplingRequest
    elicitation: ElicitationRequest
    roots_request: ListRootsRequest
    notification: ServerNotification
    pong: PingResponse
    error: ErrorResponse
    def __init__(self, request_id: _Optional[int] = ..., initialize: _Optional[_Union[InitializeResponse, _Mapping]] = ..., list_tools: _Optional[_Union[ListToolsResponse, _Mapping]] = ..., call_tool: _Optional[_Union[CallToolResponse, _Mapping]] = ..., list_resources: _Optional[_Union[ListResourcesResponse, _Mapping]] = ..., read_resource: _Optional[_Union[ReadResourceResponse, _Mapping]] = ..., list_resource_templates: _Optional[_Union[ListResourceTemplatesResponse, _Mapping]] = ..., list_prompts: _Optional[_Union[ListPromptsResponse, _Mapping]] = ..., get_prompt: _Optional[_Union[GetPromptResponse, _Mapping]] = ..., complete: _Optional[_Union[CompleteResponse, _Mapping]] = ..., sampling: _Optional[_Union[SamplingRequest, _Mapping]] = ..., elicitation: _Optional[_Union[ElicitationRequest, _Mapping]] = ..., roots_request: _Optional[_Union[ListRootsRequest, _Mapping]] = ..., notification: _Optional[_Union[ServerNotification, _Mapping]] = ..., pong: _Optional[_Union[PingResponse, _Mapping]] = ..., error: _Optional[_Union[ErrorResponse, _Mapping]] = ...) -> None: ...

class InitializeRequest(_message.Message):
    __slots__ = ("client_name", "client_version", "capabilities")
    CLIENT_NAME_FIELD_NUMBER: _ClassVar[int]
    CLIENT_VERSION_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    client_name: str
    client_version: str
    capabilities: ClientCapabilities
    def __init__(self, client_name: _Optional[str] = ..., client_version: _Optional[str] = ..., capabilities: _Optional[_Union[ClientCapabilities, _Mapping]] = ...) -> None: ...

class InitializeResponse(_message.Message):
    __slots__ = ("server_name", "server_version", "capabilities")
    SERVER_NAME_FIELD_NUMBER: _ClassVar[int]
    SERVER_VERSION_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    server_name: str
    server_version: str
    capabilities: ServerCapabilities
    def __init__(self, server_name: _Optional[str] = ..., server_version: _Optional[str] = ..., capabilities: _Optional[_Union[ServerCapabilities, _Mapping]] = ...) -> None: ...

class InitializedAck(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ClientCapabilities(_message.Message):
    __slots__ = ("sampling", "elicitation", "roots")
    SAMPLING_FIELD_NUMBER: _ClassVar[int]
    ELICITATION_FIELD_NUMBER: _ClassVar[int]
    ROOTS_FIELD_NUMBER: _ClassVar[int]
    sampling: bool
    elicitation: bool
    roots: bool
    def __init__(self, sampling: bool = ..., elicitation: bool = ..., roots: bool = ...) -> None: ...

class ServerCapabilities(_message.Message):
    __slots__ = ("tools", "tools_list_changed", "resources", "prompts")
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    TOOLS_LIST_CHANGED_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    PROMPTS_FIELD_NUMBER: _ClassVar[int]
    tools: bool
    tools_list_changed: bool
    resources: bool
    prompts: bool
    def __init__(self, tools: bool = ..., tools_list_changed: bool = ..., resources: bool = ..., prompts: bool = ...) -> None: ...

class ListToolsRequest(_message.Message):
    __slots__ = ("cursor",)
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    cursor: str
    def __init__(self, cursor: _Optional[str] = ...) -> None: ...

class ListToolsResponse(_message.Message):
    __slots__ = ("tools", "next_cursor")
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    tools: _containers.RepeatedCompositeFieldContainer[ToolDefinition]
    next_cursor: str
    def __init__(self, tools: _Optional[_Iterable[_Union[ToolDefinition, _Mapping]]] = ..., next_cursor: _Optional[str] = ...) -> None: ...

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

class ToolDefinition(_message.Message):
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

class CallToolRequest(_message.Message):
    __slots__ = ("name", "arguments")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    name: str
    arguments: str
    def __init__(self, name: _Optional[str] = ..., arguments: _Optional[str] = ...) -> None: ...

class CallToolResponse(_message.Message):
    __slots__ = ("content", "is_error")
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    IS_ERROR_FIELD_NUMBER: _ClassVar[int]
    content: _containers.RepeatedCompositeFieldContainer[ContentItem]
    is_error: bool
    def __init__(self, content: _Optional[_Iterable[_Union[ContentItem, _Mapping]]] = ..., is_error: bool = ...) -> None: ...

class ListResourcesRequest(_message.Message):
    __slots__ = ("cursor",)
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    cursor: str
    def __init__(self, cursor: _Optional[str] = ...) -> None: ...

class ListResourcesResponse(_message.Message):
    __slots__ = ("resources", "next_cursor")
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    resources: _containers.RepeatedCompositeFieldContainer[ResourceDefinition]
    next_cursor: str
    def __init__(self, resources: _Optional[_Iterable[_Union[ResourceDefinition, _Mapping]]] = ..., next_cursor: _Optional[str] = ...) -> None: ...

class ResourceDefinition(_message.Message):
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

class ReadResourceRequest(_message.Message):
    __slots__ = ("uri",)
    URI_FIELD_NUMBER: _ClassVar[int]
    uri: str
    def __init__(self, uri: _Optional[str] = ...) -> None: ...

class ReadResourceResponse(_message.Message):
    __slots__ = ("content",)
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    content: _containers.RepeatedCompositeFieldContainer[ContentItem]
    def __init__(self, content: _Optional[_Iterable[_Union[ContentItem, _Mapping]]] = ...) -> None: ...

class SubscribeResourceReq(_message.Message):
    __slots__ = ("uri",)
    URI_FIELD_NUMBER: _ClassVar[int]
    uri: str
    def __init__(self, uri: _Optional[str] = ...) -> None: ...

class ListResourceTemplatesRequest(_message.Message):
    __slots__ = ("cursor",)
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    cursor: str
    def __init__(self, cursor: _Optional[str] = ...) -> None: ...

class ListResourceTemplatesResponse(_message.Message):
    __slots__ = ("templates", "next_cursor")
    TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    templates: _containers.RepeatedCompositeFieldContainer[ResourceTemplateDefinition]
    next_cursor: str
    def __init__(self, templates: _Optional[_Iterable[_Union[ResourceTemplateDefinition, _Mapping]]] = ..., next_cursor: _Optional[str] = ...) -> None: ...

class ResourceTemplateDefinition(_message.Message):
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

class ListPromptsRequest(_message.Message):
    __slots__ = ("cursor",)
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    cursor: str
    def __init__(self, cursor: _Optional[str] = ...) -> None: ...

class ListPromptsResponse(_message.Message):
    __slots__ = ("prompts", "next_cursor")
    PROMPTS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    prompts: _containers.RepeatedCompositeFieldContainer[PromptDefinition]
    next_cursor: str
    def __init__(self, prompts: _Optional[_Iterable[_Union[PromptDefinition, _Mapping]]] = ..., next_cursor: _Optional[str] = ...) -> None: ...

class PromptDefinition(_message.Message):
    __slots__ = ("name", "description", "arguments")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    arguments: _containers.RepeatedCompositeFieldContainer[PromptArgument]
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., arguments: _Optional[_Iterable[_Union[PromptArgument, _Mapping]]] = ...) -> None: ...

class PromptArgument(_message.Message):
    __slots__ = ("name", "description", "required")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    required: bool
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., required: bool = ...) -> None: ...

class GetPromptRequest(_message.Message):
    __slots__ = ("name", "arguments")
    class ArgumentsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    name: str
    arguments: _containers.ScalarMap[str, str]
    def __init__(self, name: _Optional[str] = ..., arguments: _Optional[_Mapping[str, str]] = ...) -> None: ...

class GetPromptResponse(_message.Message):
    __slots__ = ("messages",)
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    messages: _containers.RepeatedCompositeFieldContainer[PromptMessage]
    def __init__(self, messages: _Optional[_Iterable[_Union[PromptMessage, _Mapping]]] = ...) -> None: ...

class PromptMessage(_message.Message):
    __slots__ = ("role", "content")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    role: str
    content: ContentItem
    def __init__(self, role: _Optional[str] = ..., content: _Optional[_Union[ContentItem, _Mapping]] = ...) -> None: ...

class CompleteRequest(_message.Message):
    __slots__ = ("ref", "argument")
    REF_FIELD_NUMBER: _ClassVar[int]
    ARGUMENT_FIELD_NUMBER: _ClassVar[int]
    ref: CompletionRef
    argument: CompletionArg
    def __init__(self, ref: _Optional[_Union[CompletionRef, _Mapping]] = ..., argument: _Optional[_Union[CompletionArg, _Mapping]] = ...) -> None: ...

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

class CompleteResponse(_message.Message):
    __slots__ = ("values", "has_more", "total")
    VALUES_FIELD_NUMBER: _ClassVar[int]
    HAS_MORE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[str]
    has_more: bool
    total: int
    def __init__(self, values: _Optional[_Iterable[str]] = ..., has_more: bool = ..., total: _Optional[int] = ...) -> None: ...

class ModelHint(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class ModelPreferences(_message.Message):
    __slots__ = ("hints", "cost_priority", "speed_priority", "intelligence_priority")
    HINTS_FIELD_NUMBER: _ClassVar[int]
    COST_PRIORITY_FIELD_NUMBER: _ClassVar[int]
    SPEED_PRIORITY_FIELD_NUMBER: _ClassVar[int]
    INTELLIGENCE_PRIORITY_FIELD_NUMBER: _ClassVar[int]
    hints: _containers.RepeatedCompositeFieldContainer[ModelHint]
    cost_priority: float
    speed_priority: float
    intelligence_priority: float
    def __init__(self, hints: _Optional[_Iterable[_Union[ModelHint, _Mapping]]] = ..., cost_priority: _Optional[float] = ..., speed_priority: _Optional[float] = ..., intelligence_priority: _Optional[float] = ...) -> None: ...

class SamplingTool(_message.Message):
    __slots__ = ("name", "description", "input_schema")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    INPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    input_schema: str
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., input_schema: _Optional[str] = ...) -> None: ...

class SamplingRequest(_message.Message):
    __slots__ = ("messages", "system_prompt", "max_tokens", "model_preferences", "tools", "tool_choice")
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_PROMPT_FIELD_NUMBER: _ClassVar[int]
    MAX_TOKENS_FIELD_NUMBER: _ClassVar[int]
    MODEL_PREFERENCES_FIELD_NUMBER: _ClassVar[int]
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    TOOL_CHOICE_FIELD_NUMBER: _ClassVar[int]
    messages: _containers.RepeatedCompositeFieldContainer[SamplingMessage]
    system_prompt: str
    max_tokens: int
    model_preferences: ModelPreferences
    tools: _containers.RepeatedCompositeFieldContainer[SamplingTool]
    tool_choice: str
    def __init__(self, messages: _Optional[_Iterable[_Union[SamplingMessage, _Mapping]]] = ..., system_prompt: _Optional[str] = ..., max_tokens: _Optional[int] = ..., model_preferences: _Optional[_Union[ModelPreferences, _Mapping]] = ..., tools: _Optional[_Iterable[_Union[SamplingTool, _Mapping]]] = ..., tool_choice: _Optional[str] = ...) -> None: ...

class SamplingMessage(_message.Message):
    __slots__ = ("role", "content")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    role: str
    content: _containers.RepeatedCompositeFieldContainer[ContentItem]
    def __init__(self, role: _Optional[str] = ..., content: _Optional[_Iterable[_Union[ContentItem, _Mapping]]] = ...) -> None: ...

class SamplingResponse(_message.Message):
    __slots__ = ("role", "content", "model", "stop_reason")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    STOP_REASON_FIELD_NUMBER: _ClassVar[int]
    role: str
    content: _containers.RepeatedCompositeFieldContainer[ContentItem]
    model: str
    stop_reason: str
    def __init__(self, role: _Optional[str] = ..., content: _Optional[_Iterable[_Union[ContentItem, _Mapping]]] = ..., model: _Optional[str] = ..., stop_reason: _Optional[str] = ...) -> None: ...

class ElicitationRequest(_message.Message):
    __slots__ = ("message", "schema")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SCHEMA_FIELD_NUMBER: _ClassVar[int]
    message: str
    schema: str
    def __init__(self, message: _Optional[str] = ..., schema: _Optional[str] = ...) -> None: ...

class ElicitationResponse(_message.Message):
    __slots__ = ("action", "content")
    ACTION_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    action: str
    content: str
    def __init__(self, action: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class ListRootsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListRootsResponse(_message.Message):
    __slots__ = ("roots",)
    ROOTS_FIELD_NUMBER: _ClassVar[int]
    roots: _containers.RepeatedCompositeFieldContainer[Root]
    def __init__(self, roots: _Optional[_Iterable[_Union[Root, _Mapping]]] = ...) -> None: ...

class Root(_message.Message):
    __slots__ = ("uri", "name")
    URI_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    uri: str
    name: str
    def __init__(self, uri: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class ServerNotification(_message.Message):
    __slots__ = ("type", "payload")
    class Type(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        TOOLS_LIST_CHANGED: _ClassVar[ServerNotification.Type]
        RESOURCES_LIST_CHANGED: _ClassVar[ServerNotification.Type]
        RESOURCE_UPDATED: _ClassVar[ServerNotification.Type]
        PROMPTS_LIST_CHANGED: _ClassVar[ServerNotification.Type]
        PROGRESS: _ClassVar[ServerNotification.Type]
        LOG: _ClassVar[ServerNotification.Type]
    TOOLS_LIST_CHANGED: ServerNotification.Type
    RESOURCES_LIST_CHANGED: ServerNotification.Type
    RESOURCE_UPDATED: ServerNotification.Type
    PROMPTS_LIST_CHANGED: ServerNotification.Type
    PROGRESS: ServerNotification.Type
    LOG: ServerNotification.Type
    TYPE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    type: ServerNotification.Type
    payload: str
    def __init__(self, type: _Optional[_Union[ServerNotification.Type, str]] = ..., payload: _Optional[str] = ...) -> None: ...

class ClientNotification(_message.Message):
    __slots__ = ("type", "payload")
    class Type(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        ROOTS_LIST_CHANGED: _ClassVar[ClientNotification.Type]
    ROOTS_LIST_CHANGED: ClientNotification.Type
    TYPE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    type: ClientNotification.Type
    payload: str
    def __init__(self, type: _Optional[_Union[ClientNotification.Type, str]] = ..., payload: _Optional[str] = ...) -> None: ...

class ContentItem(_message.Message):
    __slots__ = ("type", "text", "data", "mime_type", "uri", "tool_use_id", "tool_name", "tool_input", "tool_result_id")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    URI_FIELD_NUMBER: _ClassVar[int]
    TOOL_USE_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    TOOL_INPUT_FIELD_NUMBER: _ClassVar[int]
    TOOL_RESULT_ID_FIELD_NUMBER: _ClassVar[int]
    type: str
    text: str
    data: bytes
    mime_type: str
    uri: str
    tool_use_id: str
    tool_name: str
    tool_input: str
    tool_result_id: str
    def __init__(self, type: _Optional[str] = ..., text: _Optional[str] = ..., data: _Optional[bytes] = ..., mime_type: _Optional[str] = ..., uri: _Optional[str] = ..., tool_use_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., tool_input: _Optional[str] = ..., tool_result_id: _Optional[str] = ...) -> None: ...

class PingRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class PingResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class CancelRequest(_message.Message):
    __slots__ = ("target_request_id",)
    TARGET_REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    target_request_id: int
    def __init__(self, target_request_id: _Optional[int] = ...) -> None: ...

class ErrorResponse(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: int
    message: str
    def __init__(self, code: _Optional[int] = ..., message: _Optional[str] = ...) -> None: ...
