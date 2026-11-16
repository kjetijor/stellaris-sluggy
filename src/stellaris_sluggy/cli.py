"""Command-line interface for stellaris-sluggy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence

from .parser import (
    DEFAULT_MODIFIER,
    PlayerContext,
    SaveMeta,
    SystemInfo,
    SystemWildlifeMatch,
    analyze_save,
    marker_for_index,
    read_save_contents,
    render_ascii_starmap,
    resolve_save_target,
    summarize_all_modifiers,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stellaris-sluggy",
        description=(
            "Scan Stellaris save files (.sav or gamestate) for star systems "
            "containing planets with the 'dangerous_wildlife' modifier."
        ),
    )
    parser.add_argument(
        "save_path",
        nargs="?",
        default=None,
        help=(
            "Path to a Stellaris .sav file, extracted gamestate file, or save directory. "
            "Defaults to the newest .sav file in the Stellaris local or Steam Cloud save folder."
        ),
    )
    parser.add_argument(
        "-m",
        "--modifier",
        default=DEFAULT_MODIFIER,
        help=(
            f"Modifier key or substring to search for (default: '{DEFAULT_MODIFIER}')."
        ),
    )
    parser.add_argument(
        "-b",
        "--include-blockers",
        action="store_true",
        help="Also include dangerous wildlife blocker deposits (d_dangerous_wildlife_blocker).",
    )
    parser.add_argument(
        "-r",
        "--regex",
        action="store_true",
        help="Treat --modifier as a case-insensitive regular expression.",
    )
    parser.add_argument(
        "--no-map",
        action="store_true",
        help="Omit the ASCII star map from the output.",
    )
    parser.add_argument(
        "--list-modifiers",
        action="store_true",
        help="List all distinct planet modifiers in the save file with counts and exit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON.",
    )
    return parser


def _format_coords(x: float | None, y: float | None) -> str:
    if x is None or y is None:
        return "N/A"
    return f"({x:.1f}, {y:.1f})"


def _print_table_report(
    meta: SaveMeta,
    matches: list[SystemWildlifeMatch],
    player_ctx: PlayerContext,
    all_systems: Iterable[SystemInfo],
    modifier_query: str,
    *,
    show_map: bool = True,
) -> None:
    save_label = str(meta.save_path) if meta.save_path else "gamestate"
    cap_sys = player_ctx.capital_system
    if cap_sys is not None:
        cap_planet_str = (
            f"{player_ctx.capital_planet_name} in "
            if player_ctx.capital_planet_name
            and player_ctx.capital_planet_name != cap_sys.name
            else ""
        )
        cap_summary = (
            f"{cap_planet_str}{cap_sys.name} (#{cap_sys.system_id}) "
            f"at {_format_coords(cap_sys.x, cap_sys.y)}"
        )
    else:
        cap_summary = "Unknown"

    print(f"Save:    {save_label}")
    print(f"Empire:  {meta.empire_name}  |  Date: {meta.date}  |  Version: {meta.version}")
    print(f"Capital: {cap_summary}")
    print(f"Filter:  {modifier_query}")
    print("-" * 116)

    total_planets = sum(len(m.matching_planets) for m in matches)
    if not matches:
        print(f"No systems found matching modifier '{modifier_query}'.")
        return

    print(
        f"Found {len(matches)} system(s) ({total_planets} planet(s)) with '{modifier_query}':\n"
    )
    header = (
        f"{'Map':<5} {'System (ID)':<22} {'Coords':<16} {'Ownership':<18} "
        f"{'Surveyed':<12} {'Planet (ID)':<22} {'Class (Size)':<16} {'Modifiers'}"
    )
    print(header)
    print("-" * len(header))

    for sys_idx, match in enumerate(matches):
        marker = f"[{marker_for_index(sys_idx)}]"
        sys_info = match.system
        sys_label = f"{sys_info.name} (#{sys_info.system_id})"
        coords_label = _format_coords(sys_info.x, sys_info.y)
        owner_label = sys_info.ownership_label

        if match.matching_planets:
            for p_idx, planet in enumerate(match.matching_planets):
                p_label = f"{planet.name} (#{planet.planet_id})"
                size_str = f"s{planet.planet_size}" if planet.planet_size is not None else "?"
                class_label = f"{planet.planet_class} ({size_str})"
                survey_label = (
                    "Yes" if (planet.surveyed or sys_info.surveyed) else "No"
                )
                mods_str = ", ".join(planet.modifiers)
                print(
                    f"{marker if p_idx == 0 else '':<5} "
                    f"{sys_label if p_idx == 0 else '':<22} "
                    f"{coords_label if p_idx == 0 else '':<16} "
                    f"{owner_label if p_idx == 0 else '':<18} "
                    f"{survey_label:<12} "
                    f"{p_label:<22} "
                    f"{class_label:<16} "
                    f"{mods_str}"
                )
        elif match.matching_system_modifiers:
            survey_label = "Yes" if sys_info.surveyed else "No"
            mods_str = ", ".join(match.matching_system_modifiers)
            print(
                f"{marker:<5} {sys_label:<22} {coords_label:<16} {owner_label:<18} "
                f"{survey_label:<12} {'[System Modifier]':<22} "
                f"{sys_info.star_class:<16} {mods_str}"
            )

    if show_map:
        print()
        print(
            render_ascii_starmap(
                matches=matches,
                player_ctx=player_ctx,
                all_systems=all_systems,
            )
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        target_path: Path = resolve_save_target(args.save_path)
        gamestate, meta = read_save_contents(target_path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.list_modifiers:
        counts = summarize_all_modifiers(
            gamestate, include_blockers=args.include_blockers
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "save": str(meta.save_path) if meta.save_path else None,
                        "empire": meta.empire_name,
                        "date": meta.date,
                        "version": meta.version,
                        "modifiers": counts,
                    },
                    indent=2,
                )
            )
        else:
            print(
                f"Save: {meta.save_path} ({meta.empire_name}, {meta.date}, {meta.version})"
            )
            print(f"{'Modifier':<40} {'Planets':>8}")
            print("-" * 50)
            for mod, count in counts.items():
                print(f"{mod:<40} {count:>8}")
        return 0

    matches, player_ctx, all_systems = analyze_save(
        gamestate,
        modifier_pattern=args.modifier,
        include_blockers=args.include_blockers,
        regex=args.regex,
    )

    if args.json:
        cap_sys = player_ctx.capital_system
        payload = {
            "save": str(meta.save_path) if meta.save_path else None,
            "empire": meta.empire_name,
            "date": meta.date,
            "version": meta.version,
            "player_country_id": player_ctx.country_id,
            "capital": (
                {
                    "system_id": cap_sys.system_id,
                    "system_name": cap_sys.name,
                    "planet_id": player_ctx.capital_planet_id,
                    "planet_name": player_ctx.capital_planet_name,
                    "coordinates": {"x": cap_sys.x, "y": cap_sys.y},
                }
                if cap_sys is not None
                else None
            ),
            "query": args.modifier,
            "system_count": len(matches),
            "planet_count": sum(len(m.matching_planets) for m in matches),
            "systems": [
                {
                    "map_marker": marker_for_index(idx),
                    "system_id": m.system.system_id,
                    "system_name": m.system.name,
                    "star_class": m.system.star_class,
                    "coordinates": {"x": m.system.x, "y": m.system.y},
                    "owner_id": m.system.owner_id,
                    "owned_by_player": m.system.is_owned_by_player,
                    "ownership": m.system.ownership_label,
                    "surveyed": m.system.surveyed,
                    "system_modifiers": m.matching_system_modifiers,
                    "planets": [
                        {
                            "planet_id": p.planet_id,
                            "planet_name": p.name,
                            "planet_class": p.planet_class,
                            "planet_size": p.planet_size,
                            "owner_id": p.owner_id,
                            "surveyed": p.surveyed or m.system.surveyed,
                            "modifiers": p.modifiers,
                        }
                        for p in m.matching_planets
                    ],
                }
                for idx, m in enumerate(matches)
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    _print_table_report(
        meta,
        matches,
        player_ctx,
        all_systems.values(),
        args.modifier,
        show_map=not args.no_map,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
