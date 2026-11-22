"""Stable object-store key helpers for catalog media.

Episode layout:
  catalog/<slug>/sXXeYY/original.mp4
  catalog/<slug>/sXXeYY/poster.jpg
  catalog/<slug>/sXXeYY/moments/moment_01.jpg
  catalog/<slug>/sXXeYY/brands/<brand_slug>/<placement_id>/clip.mp4
  catalog/<slug>/sXXeYY/brands/<brand_slug>/<placement_id>/frame.jpg
  catalog/<slug>/sXXeYY/brands/<brand_slug>/<placement_id>/edited.jpg

Title poster: catalog/<slug>/poster.jpg
Brand assets (unchanged): brands/<brand_id>/<filename>
James uploads (unchanged): movies/<filename>
"""

from __future__ import annotations

import re


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "title"


def brand_slug(name: str, *, fallback: str = "brand") -> str:
    return slugify(name) or slugify(fallback) or "brand"


def episode_prefix(slug: str, season: int, episode: int) -> str:
    """Stable object prefix so a preload can land before the row exists."""
    return f"catalog/{slug}/s{int(season):02d}e{int(episode):02d}"


def original_video_key(slug: str, season: int, episode: int) -> str:
    return f"{episode_prefix(slug, season, episode)}/original.mp4"


def episode_poster_key(slug: str, season: int, episode: int) -> str:
    return f"{episode_prefix(slug, season, episode)}/poster.jpg"


def title_poster_key(slug: str) -> str:
    return f"catalog/{slug}/poster.jpg"


def moment_frame_key(slug: str, season: int, episode: int, index: int) -> str:
    return f"{episode_prefix(slug, season, episode)}/moments/moment_{int(index):02d}.jpg"


def placement_prefix(
    slug: str,
    season: int,
    episode: int,
    brand_slug_val: str,
    placement_id: str,
) -> str:
    return (
        f"{episode_prefix(slug, season, episode)}/brands/"
        f"{brand_slug_val}/{placement_id}"
    )


def placement_clip_key(
    slug: str,
    season: int,
    episode: int,
    brand_slug_val: str,
    placement_id: str,
) -> str:
    return (
        f"{placement_prefix(slug, season, episode, brand_slug_val, placement_id)}"
        "/clip.mp4"
    )


def placement_frame_key(
    slug: str,
    season: int,
    episode: int,
    brand_slug_val: str,
    placement_id: str,
) -> str:
    return (
        f"{placement_prefix(slug, season, episode, brand_slug_val, placement_id)}"
        "/frame.jpg"
    )


def placement_edited_key(
    slug: str,
    season: int,
    episode: int,
    brand_slug_val: str,
    placement_id: str,
) -> str:
    return (
        f"{placement_prefix(slug, season, episode, brand_slug_val, placement_id)}"
        "/edited.jpg"
    )


def brand_asset_key(brand_id: str, filename: str) -> str:
    return f"brands/{brand_id}/{filename}"


# ---------------------------------------------------------------------------
# Legacy keys (pre-layout migration)
# ---------------------------------------------------------------------------


def legacy_video_key(slug: str, season: int, episode: int, ext: str = ".mp4") -> str:
    suffix = ext if ext.startswith(".") else f".{ext}"
    return f"{episode_prefix(slug, season, episode)}/video{suffix}"


def legacy_placement_clip_key(placement_id: str) -> str:
    return f"placements/{placement_id}/clip.mp4"


def legacy_placement_edited_key(placement_id: str) -> str:
    return f"placements/{placement_id}/edited_frame.jpg"


def legacy_moment_frame_key(episode_id: str, index: int) -> str:
    return f"moments/{episode_id}/moment_{int(index):02d}.jpg"


# Back-compat aliases used across the codebase before the rename.
catalog_episode_prefix = episode_prefix
catalog_video_key = original_video_key
catalog_poster_key = episode_poster_key
catalog_title_poster_key = title_poster_key
