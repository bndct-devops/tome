import pytest
import respx
from httpx import Response

from backend.services.safe_fetch import (
    MAX_COVER_BYTES,
    UnsafeURLError,
    _is_public_ip,
    _validate_url,
    fetch_safe_image,
)


class TestIsPublicIP:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
        "169.254.169.254", "::1", "fe80::1", "224.0.0.1",
    ])
    def test_private_rejected(self, ip):
        assert _is_public_ip(ip) is False

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
    def test_public_accepted(self, ip):
        assert _is_public_ip(ip) is True


class TestValidateURL:
    def test_rejects_file_scheme(self):
        with pytest.raises(UnsafeURLError):
            _validate_url("file:///etc/passwd")

    def test_rejects_gopher(self):
        with pytest.raises(UnsafeURLError):
            _validate_url("gopher://example.com/")

    def test_rejects_localhost(self):
        with pytest.raises(UnsafeURLError):
            _validate_url("http://localhost/")

    def test_rejects_aws_metadata(self):
        with pytest.raises(UnsafeURLError):
            _validate_url("http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
class TestFetchSafeImage:
    @respx.mock
    async def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.safe_fetch.socket.getaddrinfo",
            lambda host, port: [(0, 0, 0, "", ("8.8.8.8", 0))],
        )
        respx.get("https://covers.openlibrary.org/b/id/123.jpg").mock(
            return_value=Response(200, content=b"\x89PNG...", headers={"Content-Type": "image/png"}),
        )
        data = await fetch_safe_image("https://covers.openlibrary.org/b/id/123.jpg")
        assert data.startswith(b"\x89PNG")

    @respx.mock
    async def test_rejects_non_image_content_type(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.safe_fetch.socket.getaddrinfo",
            lambda host, port: [(0, 0, 0, "", ("8.8.8.8", 0))],
        )
        respx.get("https://example.com/not-an-image").mock(
            return_value=Response(200, content=b"<html>", headers={"Content-Type": "text/html"}),
        )
        with pytest.raises(UnsafeURLError, match="image content-type"):
            await fetch_safe_image("https://example.com/not-an-image")

    @respx.mock
    async def test_rejects_oversize(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.safe_fetch.socket.getaddrinfo",
            lambda host, port: [(0, 0, 0, "", ("8.8.8.8", 0))],
        )
        huge = b"x" * (MAX_COVER_BYTES + 1)
        respx.get("https://example.com/big.jpg").mock(
            return_value=Response(200, content=huge, headers={"Content-Type": "image/jpeg"}),
        )
        with pytest.raises(UnsafeURLError, match="too large"):
            await fetch_safe_image("https://example.com/big.jpg")
