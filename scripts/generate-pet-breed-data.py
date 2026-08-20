#!/usr/bin/env python3
"""Convert BattlePetBreedID's PetData.lua into the compact JSON used by the API."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


BASE_STATS_RE = re.compile(
    r"BasePetStats\[(\d+)]\s*=\s*\{([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)}"
)
BREEDS_RE = re.compile(r"BreedsPerSpecies\[(\d+)]\s*=\s*\{([^}]*)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Path to BattlePetBreedID/PetData.lua")
    parser.add_argument("output", type=Path, help="Destination JSON path")
    args = parser.parse_args()

    source = args.source.read_text(encoding="utf-8")
    species: dict[str, dict[str, object]] = {}
    for match in BASE_STATS_RE.finditer(source):
        species[match.group(1)] = {
            "stats": [float(match.group(index)) for index in range(2, 5)],
        }
    for match in BREEDS_RE.finditer(source):
        entry = species.get(match.group(1))
        if entry is not None:
            entry["breeds"] = [int(value) for value in re.findall(r"\d+", match.group(2))]

    payload = {
        "source": "https://github.com/MMOSimca/BattlePetBreedID/blob/main/PetData.lua",
        "sourcePatch": "12.1.0",
        "species": species,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
