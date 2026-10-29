"""Filters operate on one captured item link, never on a representative of a group.

Bonus 40/41/42/43: https://www.wowhead.com/tooltips (Gear Augmentations).
12.1 prismatic socket bonus 13695 is verified against captured links and tooltip;
13696 is NOT a socket. No network request is made while serving these filters.
"""
from dataclasses import dataclass

from app.services.market_queries import SECONDARY_STAT_LABELS

TERTIARY_LABELS = {"avoidance": "闪避", "leech": "吸血", "speed": "加速", "indestructible": "永不磨损"}
STAT_OPTIONS = [{"value": f"{a}-{b}", "label": f"{an} > {bn}"}
                for a, an in SECONDARY_STAT_LABELS.items()
                for b, bn in SECONDARY_STAT_LABELS.items() if a != b]


@dataclass(frozen=True)
class VariantFilters:
    difficulty: str = ""
    stats: str = ""
    socket: str = ""
    tertiary: str = ""

    def __post_init__(self):
        allowed = ({"", "3", "4", "5", "6"}, {""} | {o['value'] for o in STAT_OPTIONS},
                   {"", "yes", "no"}, {"", "any", "none"} | set(TERTIARY_LABELS))
        if any(value not in choices for value, choices in zip(
                (self.difficulty, self.stats, self.socket, self.tertiary), allowed)):
            raise ValueError('Invalid equipment filter')

    @property
    def active(self):
        return bool(self.difficulty or self.stats or self.socket or self.tertiary)

    def matches(self, variant):
        if self.difficulty and str(variant.get('itemContext')) != self.difficulty:
            return False
        if self.stats and variant.get('statKey') != self.stats:
            return False
        if (self.socket or self.tertiary) and not variant.get('variantKnown'):
            return False
        if self.socket and variant['hasSocket'] != (self.socket == 'yes'):
            return False
        tertiary = variant.get('tertiaryStats', [])
        if self.tertiary == 'any' and not tertiary:
            return False
        if self.tertiary == 'none' and tertiary:
            return False
        if self.tertiary in TERTIARY_LABELS and self.tertiary not in tertiary:
            return False
        return True

    def public(self):
        return dict(difficulty=self.difficulty, stats=self.stats, socket=self.socket, tertiary=self.tertiary)
