"""Multi-modal ingestion: image extraction + vision descriptions.

Extracts images from PDFs and DOCX, sends them to a vision model,
and returns text descriptions to be indexed as chunks alongside the
source document.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import pathlib
from typing import Any

logger = logging.getLogger("rag-bridge.vision")

VISION_ENABLED = os.getenv("VISION_ENABLED", "true").lower() == "true"
VISION_MAX_IMAGES_PER_DOC = int(os.getenv("VISION_MAX_IMAGES_PER_DOC", "5"))
VISION_MIN_IMAGE_BYTES = int(os.getenv("VISION_MIN_IMAGE_BYTES", "5000"))

VISION_SYSTEM_PROMPT = """Describe this image concisely for indexing in a search engine.
Focus on: what the image shows, any text visible, diagram structure, data values.
Keep the description under 300 words. Be factual, not interpretive."""


def extract_images_from_pdf(path: pathlib.Path) -> list[dict[str, Any]]:
    """Extract images from a PDF file. Returns list of {data: bytes, page: int, format: str}."""
    if not VISION_ENABLED:
        return []
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    images = []
    try:
        reader = PdfReader(str(path))
        for page_num, page in enumerate(reader.pages):
            if len(images) >= VISION_MAX_IMAGES_PER_DOC:
                break
            for image_obj in page.images:
                data = image_obj.data
                if len(data) < VISION_MIN_IMAGE_BYTES:
                    continue
                images.append({"data": data, "page": page_num, "format": "png", "name": image_obj.name})
                if len(images) >= VISION_MAX_IMAGES_PER_DOC:
                    break
    except Exception as e:
        logger.warning("Failed to extract images from %s: %s", path, e)
    return images


def extract_images_from_docx(path: pathlib.Path) -> list[dict[str, Any]]:
    """Extract images from a DOCX file."""
    if not VISION_ENABLED:
        return []
    try:
        import docx
    except ImportError:
        return []
    images = []
    try:
        doc = docx.Document(str(path))
        for rel in doc.part.rels.values():
            if "image" in rel.reltype:
                data = rel.target_part.blob
                if len(data) < VISION_MIN_IMAGE_BYTES:
                    continue
                content_type = rel.target_part.content_type or "image/png"
                fmt = content_type.split("/")[-1]
                images.append({"data": data, "page": 0, "format": fmt, "name": rel.target_ref})
                if len(images) >= VISION_MAX_IMAGES_PER_DOC:
                    break
    except Exception as e:
        logger.warning("Failed to extract images from %s: %s", path, e)
    return images


def build_vision_messages(image_data: bytes, image_format: str = "png", context: str = "") -> list[dict[str, str | list]]:
    """Build messages for the vision model call."""
    b64 = base64.b64encode(image_data).decode("utf-8")
    media_type = f"image/{image_format}" if image_format in ("png", "jpeg", "jpg", "gif", "webp") else "image/png"
    user_content: list[dict[str, Any]] = []
    if context:
        user_content.append({"type": "text", "text": f"Document context: {context}"})
    user_content.append({
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{b64}"},
    })
    user_content.append({"type": "text", "text": "Describe this image for search indexing."})
    return [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg"}


def is_image_file(path: pathlib.Path) -> bool:
    """Check if a file is a standalone image (not a document with embedded images)."""
    return path.suffix.lower() in IMAGE_SUFFIXES


def extract_standalone_image(path: pathlib.Path) -> list[dict[str, Any]]:
    """Read a standalone image file and return it for vision processing."""
    if not VISION_ENABLED:
        return []
    try:
        data = path.read_bytes()
        if len(data) < VISION_MIN_IMAGE_BYTES:
            return []
        suffix = path.suffix.lower().lstrip(".")
        # Normalise format names
        fmt_map = {"jpg": "jpeg", "tif": "tiff", "svg": "svg+xml"}
        fmt = fmt_map.get(suffix, suffix)
        return [{"data": data, "page": 0, "format": fmt, "name": path.name}]
    except Exception as e:
        logger.warning("Failed to read image %s: %s", path, e)
        return []


def extract_images(path: pathlib.Path) -> list[dict[str, Any]]:
    """Extract images from a file based on its suffix."""
    if not VISION_ENABLED:
        return []
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return extract_standalone_image(path)
    if suffix == ".pdf":
        return extract_images_from_pdf(path)
    elif suffix == ".docx":
        return extract_images_from_docx(path)
    return []
