"""The v2 stubs exist and describe the phase 1 service."""


def test_v2_service_has_the_expected_methods():
    from rapidmcp._generated import mcp_v2_pb2

    service = mcp_v2_pb2.DESCRIPTOR.services_by_name["Mcp"]

    assert service.full_name == "mcp.v2.Mcp"
    assert sorted(m.name for m in service.methods) == [
        "CallTool",
        "Complete",
        "Discover",
        "GetPrompt",
        "ListPrompts",
        "ListResourceTemplates",
        "ListResources",
        "ListTools",
        "ReadResource",
    ]
    streaming = {m.name for m in service.methods if m.server_streaming}
    assert streaming == {"CallTool", "ReadResource", "GetPrompt"}


def test_annotation_hints_can_be_left_unset():
    from rapidmcp._generated import mcp_v2_pb2

    unset = mcp_v2_pb2.ToolAnnotations(title="t")
    explicit = mcp_v2_pb2.ToolAnnotations(destructive_hint=False)

    assert not unset.HasField("destructive_hint")
    assert explicit.HasField("destructive_hint")
