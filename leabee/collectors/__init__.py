from __future__ import annotations

from typing import Callable, Iterable

from ..models import RawItem


def registry() -> dict[str, Callable[..., Iterable[RawItem]]]:
    from . import boards, github, gmaps, hackernews, mastodon, reddit, tinyfish
    return {
        "reddit": reddit.collect,
        "hackernews": hackernews.collect,
        "boards": boards.collect,
        "mastodon": mastodon.collect,
        "github": github.collect,
        "gmaps": gmaps.collect,
        "tinyfish": tinyfish.collect,
    }
