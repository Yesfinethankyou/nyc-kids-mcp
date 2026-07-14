"""Registry-wide invariants for source labels.

The friendly source label lives on each Source as `display_name` (see
sources/base.py) and is collected into `SOURCE_DISPLAY_NAMES`. These tests are
the enforcement that keeps the convention honest: a new source can't ship
without a human-friendly name (the drift that left the newer sources — nypl,
qpl, snug_harbor, … — rendering as raw slugs before this).
"""

from __future__ import annotations

from nyc_events.sources import (
    ALL_SOURCES,
    ENABLED_SOURCES,
    SOURCE_DISPLAY_NAMES,
)


def test_every_source_defines_a_friendly_display_name():
    for cls in ALL_SOURCES:
        label = getattr(cls, "display_name", None)
        assert isinstance(label, str) and label.strip(), (
            f"{cls.__name__} ({getattr(cls, 'name', '?')}) is missing a "
            f"display_name — every source needs a human-friendly label"
        )
        # A friendly name is not the machine slug: slugs are lowercase_snake,
        # so guard against a lazy `display_name = name`.
        assert label != cls.name, f"{cls.__name__}: display_name must differ from slug"
        assert "_" not in label, (
            f"{cls.__name__}: display_name {label!r} looks like a slug, not a label"
        )


def test_display_name_map_covers_every_enabled_source():
    for cls in ENABLED_SOURCES:
        assert cls.name in SOURCE_DISPLAY_NAMES
        assert SOURCE_DISPLAY_NAMES[cls.name] == cls.display_name


def test_source_slugs_are_unique():
    slugs = [cls.name for cls in ALL_SOURCES]
    assert len(slugs) == len(set(slugs)), "duplicate source slug in the registry"
