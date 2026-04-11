"""Tests for FasterMCP.mount() — server composition."""

import pytest

from mcp_grpc import Client, FasterMCP
from mcp_grpc.server import _prefix_resource_uri

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_users_sub() -> FasterMCP:
    sub = FasterMCP("Users", "1.0")

    @sub.tool(description="Get a user by ID")
    async def get_user(id: int) -> str:
        return f"user:{id}"

    @sub.resource(uri="res://profile", description="User profile")
    async def profile() -> str:
        return "profile data"

    @sub.resource_template(uri_template="res://users/{id}", description="User by ID")
    async def user_by_id(id: str) -> str:
        return f"user:{id}"

    @sub.prompt(description="Greet a user")
    async def greet(name: str) -> str:
        return f"Hello, {name}!"

    @sub.completion("get_user")
    async def complete_get_user(arg_name: str, value: str) -> list[str]:
        return ["1", "2", "3"]

    return sub


# ---------------------------------------------------------------------------
# Unit tests — _prefix_resource_uri helper
# ---------------------------------------------------------------------------


def test_prefix_resource_uri_with_scheme():
    assert _prefix_resource_uri("res://greeting", "users") == "res://users/greeting"


def test_prefix_resource_uri_with_path():
    assert _prefix_resource_uri("res://items/{id}", "orders") == "res://orders/items/{id}"


def test_prefix_resource_uri_no_scheme():
    assert _prefix_resource_uri("plain/path", "users") == "users/plain/path"


def test_prefix_resource_uri_nested_path():
    assert _prefix_resource_uri("res://a/b/c", "x") == "res://x/a/b/c"


# ---------------------------------------------------------------------------
# Unit tests — registry merging (no gRPC)
# ---------------------------------------------------------------------------


def test_mount_tools_are_prefixed():
    main = FasterMCP("Main", "1.0")
    sub = make_users_sub()
    main.mount(sub, prefix="users")

    assert "users_get_user" in main._tools
    assert main._tools["users_get_user"].name == "users_get_user"
    assert "get_user" not in main._tools


def test_mount_resources_uri_prefixed():
    main = FasterMCP("Main", "1.0")
    sub = make_users_sub()
    main.mount(sub, prefix="users")

    assert "res://users/profile" in main._resources
    assert main._resources["res://users/profile"].uri == "res://users/profile"
    assert "res://profile" not in main._resources


def test_mount_resource_templates_uri_prefixed():
    main = FasterMCP("Main", "1.0")
    sub = make_users_sub()
    main.mount(sub, prefix="users")

    assert "res://users/users/{id}" in main._resource_templates
    assert (
        main._resource_templates["res://users/users/{id}"].uri_template == "res://users/users/{id}"
    )


def test_mount_prompts_are_prefixed():
    main = FasterMCP("Main", "1.0")
    sub = make_users_sub()
    main.mount(sub, prefix="users")

    assert "users_greet" in main._prompts
    assert main._prompts["users_greet"].name == "users_greet"
    assert "greet" not in main._prompts


def test_mount_completions_ref_name_prefixed():
    main = FasterMCP("Main", "1.0")
    sub = make_users_sub()
    main.mount(sub, prefix="users")

    assert "users_get_user" in main._completions
    assert main._completions["users_get_user"].ref_name == "users_get_user"
    assert "get_user" not in main._completions


def test_mount_does_not_modify_sub_server():
    main = FasterMCP("Main", "1.0")
    sub = make_users_sub()
    main.mount(sub, prefix="users")

    # Sub's own registries must be unchanged
    assert "get_user" in sub._tools
    assert sub._tools["get_user"].name == "get_user"
    assert "res://profile" in sub._resources
    assert "greet" in sub._prompts
    assert "get_user" in sub._completions


def test_mount_main_own_tools_unaffected():
    main = FasterMCP("Main", "1.0")

    @main.tool(description="Ping")
    async def ping() -> str:
        return "pong"

    sub = make_users_sub()
    main.mount(sub, prefix="users")

    assert "ping" in main._tools
    assert "users_get_user" in main._tools
    assert len(main._tools) == 2


def test_mount_multiple_prefixes_no_collision():
    main = FasterMCP("Main", "1.0")

    sub1 = FasterMCP("Sub1", "1.0")

    @sub1.tool(description="List items")
    async def list_items() -> str:
        return "sub1 items"

    sub2 = FasterMCP("Sub2", "1.0")

    @sub2.tool(description="List items")
    async def list_items() -> str:  # noqa: F811
        return "sub2 items"

    main.mount(sub1, prefix="users")
    main.mount(sub2, prefix="orders")

    assert "users_list_items" in main._tools
    assert "orders_list_items" in main._tools
    assert len(main._tools) == 2


# ---------------------------------------------------------------------------
# Collision tests — atomic two-pass behaviour
# ---------------------------------------------------------------------------


def test_mount_tool_collision_raises():
    main = FasterMCP("Main", "1.0")

    @main.tool(description="Already registered")
    async def users_get_user() -> str:
        return "original"

    sub = make_users_sub()

    with pytest.raises(ValueError, match="users_get_user"):
        main.mount(sub, prefix="users")


def test_mount_resource_collision_raises():
    main = FasterMCP("Main", "1.0")

    @main.resource(uri="res://users/profile")
    async def existing() -> str:
        return "existing"

    sub = make_users_sub()

    with pytest.raises(ValueError, match="res://users/profile"):
        main.mount(sub, prefix="users")


def test_mount_prompt_collision_raises():
    main = FasterMCP("Main", "1.0")

    @main.prompt(description="Already here")
    async def users_greet(name: str) -> str:
        return "original"

    sub = make_users_sub()

    with pytest.raises(ValueError, match="users_greet"):
        main.mount(sub, prefix="users")


def test_mount_completion_collision_raises():
    main = FasterMCP("Main", "1.0")

    @main.completion("users_get_user")
    async def complete(arg_name: str, value: str) -> list[str]:
        return []

    sub = make_users_sub()

    with pytest.raises(ValueError, match="users_get_user"):
        main.mount(sub, prefix="users")


def test_mount_collision_is_atomic():
    """If any key collides, NO entries from sub should be written."""
    main = FasterMCP("Main", "1.0")

    sub = FasterMCP("Sub", "1.0")

    @sub.tool(description="Alpha")
    async def alpha() -> str:
        return "a"

    @sub.tool(description="Beta — will collide")
    async def beta() -> str:
        return "b"

    # Pre-register the colliding name on main
    @main.tool(description="Existing beta")
    async def p_beta() -> str:
        return "original"

    with pytest.raises(ValueError, match="p_beta"):
        main.mount(sub, prefix="p")

    # Two-pass: alpha must NOT have been written either
    assert "p_alpha" not in main._tools
    assert len(main._tools) == 1  # only the original p_beta


# ---------------------------------------------------------------------------
# gRPC integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def composed_server():
    main = FasterMCP("Main", "1.0")

    @main.tool(description="Main ping")
    async def ping() -> str:
        return "pong"

    sub = FasterMCP("Users", "1.0")

    @sub.tool(description="Get profile")
    async def get_profile() -> str:
        return "profile data"

    @sub.resource(uri="res://avatar", description="User avatar")
    async def avatar() -> str:
        return "avatar bytes"

    main.mount(sub, prefix="users")

    async with main:
        yield main


@pytest.mark.asyncio
async def test_mount_tools_served_over_grpc(composed_server):
    async with Client(f"localhost:{composed_server.port}") as client:
        result = await client.list_tools()
        names = {t.name for t in result.items}
        assert "ping" in names
        assert "users_get_profile" in names


@pytest.mark.asyncio
async def test_mount_call_mounted_tool_over_grpc(composed_server):
    async with Client(f"localhost:{composed_server.port}") as client:
        result = await client.call_tool("users_get_profile", {})
        assert result.content[0].text == "profile data"
        assert not result.is_error


@pytest.mark.asyncio
async def test_mount_main_tool_still_works_over_grpc(composed_server):
    async with Client(f"localhost:{composed_server.port}") as client:
        result = await client.call_tool("ping", {})
        assert result.content[0].text == "pong"
        assert not result.is_error


@pytest.mark.asyncio
async def test_mount_resources_served_over_grpc(composed_server):
    async with Client(f"localhost:{composed_server.port}") as client:
        result = await client.list_resources()
        uris = {r.uri for r in result.items}
        assert "res://users/avatar" in uris


@pytest.mark.asyncio
async def test_mount_read_mounted_resource_over_grpc(composed_server):
    async with Client(f"localhost:{composed_server.port}") as client:
        result = await client.read_resource("res://users/avatar")
        assert result.content[0].text == "avatar bytes"
