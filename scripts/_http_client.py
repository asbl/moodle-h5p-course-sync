"""HTTP client utilities for fetching and downloading web resources.

This module provides wrappers around urllib for HTTP requests with consistent
error handling, headers, and timeout configuration.
"""

from __future__ import annotations

import json as json_module
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
import shutil


def fetch_text(url: str) -> str:
    """Fetch plain text content from a URL.
    
    Args:
        url: The URL to fetch
        
    Returns:
        The response body as a decoded UTF-8 string
        
    Raises:
        HTTPException or URLError if the request fails
    """
    request = Request(url, headers={"User-Agent": "course-sync"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def normalize_http_url(url: str) -> str:
    """Normalize an HTTP(S) URL by percent-encoding path and query components.
    
    Ensures consistent URL formatting while preserving semantics.
    Path slashes and query delimiters are not encoded.
    
    Args:
        url: The URL to normalize
        
    Returns:
        The normalized URL string
    """
    parsed = urlparse(url)
    normalized_path = quote(parsed.path, safe="/%")
    normalized_query = quote(parsed.query, safe="=&%/:+,-_.~")
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            parsed.params,
            normalized_query,
            parsed.fragment,
        )
    )


def extract_h5p_package_url_from_activity_html(
    page_html: str, *, base_url: str = ""
) -> str:
    """Extract the H5P package URL from an embedded H5P activity iframe.
    
    Searches for an iframe with src matching H5P_EMBED_IFRAME_RE pattern and
    extracts the `url` query parameter, converting relative URLs based on base_url.
    
    Args:
        page_html: HTML content containing an H5P activity embed
        base_url: Base URL for resolving relative iframe srcs
        
    Returns:
        The H5P package URL, or empty string if not found
    """
    import html
    import re
    from scripts.config import settings
    
    app_config = settings.build_app_config()
    h5p_embed_iframe_re = app_config.h5p_embed_iframe_re
    
    unescaped_html = html.unescape(page_html)
    iframe_match = h5p_embed_iframe_re.search(unescaped_html)
    if not iframe_match:
        return ""

    iframe_src = urljoin(base_url, iframe_match.group("src"))
    iframe_query = parse_qs(urlparse(iframe_src).query)
    package_url = iframe_query.get("url", [""])[0]
    return unquote(package_url).strip()


def fetch_json(url: str) -> dict[str, Any]:
    """Fetch JSON content from a URL.
    
    Args:
        url: The URL to fetch
        
    Returns:
        The parsed JSON response as a dict
        
    Raises:
        HTTPException, URLError, or json.JSONDecodeError if the request fails
    """
    request = Request(
        url, headers={"Accept": "application/json", "User-Agent": "course-sync"}
    )
    with urlopen(request, timeout=30) as response:
        return json_module.load(response)


def download_file(url: str, destination: Path) -> None:
    """Download a file from a URL to a local destination.
    
    Args:
        url: The URL to download from
        destination: The local Path where the file should be saved
        
    Raises:
        HTTPException or URLError if the request fails
    """
    request = Request(
        normalize_http_url(url), headers={"User-Agent": "course-sync"}
    )
    with urlopen(request, timeout=60) as response, destination.open(
        "wb"
    ) as target:
        shutil.copyfileobj(response, target)
