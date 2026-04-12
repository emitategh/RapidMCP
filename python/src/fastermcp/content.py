"""Rich content types for FasterMCP tool responses.

Tool handlers may return any of:

* ``str``        — plain text (most common)
* ``Image``      — base-64 encoded image with a MIME type
* ``Audio``      — base-64 encoded audio with a MIME type
* ``list``       — any combination of the above
* anything else  — converted to its ``str()`` representation
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Image:
    """Binary image content returned from a tool.

    Args:
        data: Raw image bytes.
        mime_type: MIME type of the image (default ``"image/png"``).

    Example::

        @app.tool()
        async def screenshot() -> Image:
            raw = take_screenshot()
            return Image(data=raw, mime_type="image/png")
    """

    data: bytes
    mime_type: str = field(default="image/png")


@dataclass
class Audio:
    """Binary audio content returned from a tool.

    Args:
        data: Raw audio bytes.
        mime_type: MIME type of the audio (default ``"audio/mpeg"``).

    Example::

        @app.tool()
        async def text_to_speech(text: str) -> Audio:
            raw = tts_engine(text)
            return Audio(data=raw, mime_type="audio/mpeg")
    """

    data: bytes
    mime_type: str = field(default="audio/mpeg")
