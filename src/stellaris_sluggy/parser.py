"""Fast Clausewitz parser for Stellaris .sav and gamestate files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import zipfile
from typing import Iterable, Iterator, Sequence


DEFAULT_MODIFIER = "dangerous_wildlife"
DANGEROUS_WILDLIFE_BLOCKER = "d_dangerous_wildlife_blocker"
STELLARIS_STEAM_APP_ID = "281990"

# Standard local Stellaris save game directories across platforms
DEFAULT_SAVE_DIRS: tuple[Path, ...] = (
    Path.home() / "Documents" / "Paradox Interactive" / "Stellaris" / "save games",
    Path.home() / ".local" / "share" / "Paradox Interactive" / "Stellaris" / "save games",
)

# Steam userdata roots where Steam Cloud saves live under `<steam_id>/281990/remote/save games`
DEFAULT_STEAM_USERDATA_DIRS: tuple[Path, ...] = (
    Path.home() / "Library" / "Application Support" / "Steam" / "userdata",
    Path.home() / ".local" / "share" / "Steam" / "userdata",
    Path.home() / ".steam" / "steam" / "userdata",
)

_TOKEN_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|\{|\}')
_TOP_SECTION_RE_CACHE: dict[str, re.Pattern[str]] = {}
_ID_ENTRY_HEADER_RE = re.compile(r"(?:^|[\s\{])(\d+)\s*=\s*\{")
_TIMED_MODIFIER_RE = re.compile(
    r"timed_modifier\s*=\s*\{([^{}]*)\}", re.DOTALL
)
_MODIFIER_VALUE_RE = re.compile(r'\bmodifier\s*=\s*"([^"]+)"')
_DEPOSIT_BLOCKER_RE = re.compile(r'\b(d_[a-zA-Z0-9_]*wildlife[a-zA-Z0-9_]*)\b')
_PLANET_CLASS_RE = re.compile(r'\bplanet_class\s*=\s*"([^"]+)"')
_STAR_CLASS_RE = re.compile(r'\bstar_class\s*=\s*"([^"]+)"')
_PLANET_SIZE_RE = re.compile(r"\bplanet_size\s*=\s*(\d+)")
_OWNER_RE = re.compile(r"\bowner\s*=\s*(\d+)")
_CONTROLLER_RE = re.compile(r"\bcontroller\s*=\s*(\d+)")
_SURVEYED_BY_RE = re.compile(r"\bsurveyed_by\s*=\s*(\d+)")
_SURVEYED_FLAG_RE = re.compile(r"\bsurveyed\s*=\s*(yes|no)\b")
_SURVEYED_LIST_RE = re.compile(r"\bsurveyed\s*=\s*\{([^{}]*)\}")
_SYSTEM_ID_RE = re.compile(r"\bsystem\s*=\s*(\d+)")
_CAPITAL_RE = re.compile(r"\bcapital\s*=\s*(\d+)")
_STARTING_SYSTEM_RE = re.compile(r"\bstarting_system\s*=\s*(\d+)")
_PLAYER_COUNTRY_RE = re.compile(r"\bcountry\s*=\s*(\d+)")
_COORD_BLOCK_RE = re.compile(r"\bcoordinate\s*=\s*\{([^{}]*)\}")
_FLOAT_X_RE = re.compile(r"\bx\s*=\s*(-?\d+(?:\.\d+)?)")
_FLOAT_Y_RE = re.compile(r"\by\s*=\s*(-?\d+(?:\.\d+)?)")
_ORIGIN_RE = re.compile(r"\borigin\s*=\s*(\d+)")
_SYSTEM_PLANETS_LIST_RE = re.compile(r"\bplanet\s*=\s*\{([^{}]*)\}")
_SYSTEM_PLANET_SINGLE_RE = re.compile(r"\bplanet\s*=\s*(\d+)")
_INT_RE = re.compile(r"\b(\d+)\b")

_ROMAN_MAP: dict[str, str] = {
    f"ROMAN_{r}": r
    for r in (
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
        "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
    )
}

_MAP_MARKERS = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def get_steam_cloud_save_dirs(
    userdata_roots: Iterable[Path] = DEFAULT_STEAM_USERDATA_DIRS,
) -> list[Path]:
    """Locate Stellaris Steam Cloud save directories across all Steam user profiles."""
    cloud_dirs: list[Path] = []
    for root in userdata_roots:
        expanded = root.expanduser()
        if not expanded.is_dir():
            continue
        for remote_dir in expanded.glob(f"*/{STELLARIS_STEAM_APP_ID}/remote"):
            save_games_dir = remote_dir / "save games"
            if save_games_dir.is_dir():
                cloud_dirs.append(save_games_dir)
            elif remote_dir.is_dir():
                cloud_dirs.append(remote_dir)
    return cloud_dirs


@dataclass(slots=True)
class SaveMeta:
    """Metadata extracted from the Stellaris save's `meta` file."""

    empire_name: str = "Unknown Empire"
    date: str = "Unknown Date"
    version: str = "Unknown Version"
    save_path: Path | None = None


@dataclass(slots=True)
class PlanetInfo:
    """Information about a planet inside a star system."""

    planet_id: int
    name: str
    planet_class: str
    planet_size: int | None
    system_id: int | None
    modifiers: list[str] = field(default_factory=list)
    owner_id: int | None = None
    controller_id: int | None = None
    surveyed_by: int | None = None
    surveyed: bool = False


@dataclass(slots=True)
class SystemInfo:
    """Information about a galactic object (star system)."""

    system_id: int
    name: str
    star_class: str
    x: float | None
    y: float | None
    planet_ids: list[int] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    owner_id: int | None = None
    is_owned_by_player: bool = False
    surveyed: bool = False

    @property
    def ownership_label(self) -> str:
        if self.owner_id is None:
            return "Unowned"
        if self.is_owned_by_player:
            return "Owned (You)"
        return f"Owned (Empire #{self.owner_id})"


@dataclass(slots=True)
class PlayerContext:
    """Information about the player empire, capital homeworld, and survey state."""

    country_id: int = 0
    capital_planet_id: int | None = None
    capital_planet_name: str | None = None
    starting_system_id: int | None = None
    capital_system: SystemInfo | None = None
    surveyed_planet_ids: set[int] = field(default_factory=set)


@dataclass(slots=True)
class SystemWildlifeMatch:
    """A star system containing one or more planets (or system modifiers) matching the query."""

    system: SystemInfo
    matching_planets: list[PlanetInfo]
    matching_system_modifiers: list[str] = field(default_factory=list)


def find_latest_save(
    search_dirs: Iterable[Path] | None = None,
    steam_userdata_dirs: Iterable[Path] = DEFAULT_STEAM_USERDATA_DIRS,
) -> Path:
    """Find the most recently modified `.sav` file across local and Steam Cloud save directories."""
    env_dir = os.environ.get("STELLARIS_SAVE_DIR")
    dirs_to_check: list[Path] = []
    if env_dir:
        dirs_to_check.append(Path(env_dir).expanduser())

    if search_dirs is not None:
        dirs_to_check.extend(p.expanduser() for p in search_dirs)
    else:
        dirs_to_check.extend(p.expanduser() for p in DEFAULT_SAVE_DIRS)
        dirs_to_check.extend(get_steam_cloud_save_dirs(steam_userdata_dirs))

    candidates: list[Path] = []
    seen_files: set[Path] = set()
    for base_dir in dirs_to_check:
        if base_dir.is_dir():
            for sav in base_dir.rglob("*.sav"):
                resolved = sav.resolve()
                if resolved not in seen_files:
                    seen_files.add(resolved)
                    candidates.append(sav)

    if not candidates:
        checked_strs = [str(d) for d in dirs_to_check]
        if search_dirs is None:
            for root in steam_userdata_dirs:
                pattern_str = str(
                    root.expanduser() / "*" / STELLARIS_STEAM_APP_ID / "remote" / "save games"
                )
                if pattern_str not in checked_strs:
                    checked_strs.append(pattern_str)
        checked = ", ".join(checked_strs)
        raise FileNotFoundError(
            f"No Stellaris .sav files found in default locations ({checked}). "
            "Pass a path to a .sav file or directory explicitly."
        )

    return max(candidates, key=lambda p: p.stat().st_mtime)


def resolve_save_target(path: Path | str | None) -> Path:
    """Resolve a user-supplied path (or None) to a `.sav` or `gamestate` file."""
    if path is None:
        return find_latest_save()

    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"Save path does not exist: {target}")

    if target.is_dir():
        gamestate_file = target / "gamestate"
        if gamestate_file.is_file():
            return gamestate_file
        sav_files = list(target.rglob("*.sav"))
        if not sav_files:
            raise FileNotFoundError(
                f"No .sav or gamestate files found inside directory: {target}"
            )
        return max(sav_files, key=lambda p: p.stat().st_mtime)

    return target


def read_save_contents(path: Path) -> tuple[str, SaveMeta]:
    """Read `(gamestate_text, SaveMeta)` from a `.sav` archive or `gamestate` file."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "gamestate" not in names:
                raise ValueError(
                    f"Archive {path} does not contain a 'gamestate' entry (found: {sorted(names)})"
                )
            meta_text = (
                zf.read("meta").decode("utf-8", errors="replace")
                if "meta" in names
                else ""
            )
            gamestate_text = zf.read("gamestate").decode("utf-8", errors="replace")
            meta = parse_meta(meta_text, save_path=path)
            return gamestate_text, meta

    gamestate_text = path.read_text(encoding="utf-8", errors="replace")
    meta_file = path.parent / "meta"
    meta_text = (
        meta_file.read_text(encoding="utf-8", errors="replace")
        if meta_file.is_file()
        else ""
    )
    meta = parse_meta(meta_text, save_path=path)
    return gamestate_text, meta


def _extract_balanced_block(text: str, open_brace_idx: int) -> tuple[str, int]:
    """Given the index of an opening `{` in `text`, return `(inner_text, end_index_after_close)`."""
    depth = 0
    for match in _TOKEN_RE.finditer(text, open_brace_idx):
        tok = match.group(0)
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_idx + 1 : match.start()], match.end()
    return text[open_brace_idx + 1 :], len(text)


def extract_top_level_section(gamestate: str, section_name: str) -> str | None:
    """Extract the inner text of a top-level `<section_name>={ ... }` block."""
    pattern = _TOP_SECTION_RE_CACHE.get(section_name)
    if pattern is None:
        pattern = re.compile(
            rf"(?:^|\n){re.escape(section_name)}\s*=\s*\{{", re.MULTILINE
        )
        _TOP_SECTION_RE_CACHE[section_name] = pattern

    match = pattern.search(gamestate)
    if not match:
        return None

    open_brace_idx = match.end() - 1
    # Fast path for standard tab-indented Stellaris gamestate files where top-level closing brace is `\n}`
    close_newline = gamestate.find("\n}\n", open_brace_idx)
    if close_newline != -1:
        return gamestate[open_brace_idx + 1 : close_newline]

    inner, _ = _extract_balanced_block(gamestate, open_brace_idx)
    return inner


def iter_id_blocks(section_text: str) -> Iterator[tuple[int, str]]:
    """Yield `(entity_id, block_inner_text)` for each direct `<id>={ ... }` block in `section_text`."""
    pos = 0
    length = len(section_text)
    while pos < length:
        match = _ID_ENTRY_HEADER_RE.search(section_text, pos)
        if not match:
            break
        entity_id = int(match.group(1))
        open_brace_idx = match.end() - 1
        inner, next_pos = _extract_balanced_block(section_text, open_brace_idx)
        yield entity_id, inner
        pos = next_pos


def _clean_loc_key(raw_key: str) -> str:
    """Clean up Clausewitz localization keys into readable strings."""
    if raw_key in _ROMAN_MAP:
        return _ROMAN_MAP[raw_key]
    if raw_key.startswith("NAME_"):
        return raw_key[5:].replace("_", " ")
    if raw_key.startswith("SPEC_"):
        return raw_key[5:].replace("_", " ")
    return raw_key


def _resolve_name_block(name_inner: str) -> str:
    """Resolve a Stellaris 3.4+/4.x `name={ key="..." variables={ ... } }` block."""
    key_match = re.search(r'\bkey\s*=\s*"([^"]+)"', name_inner)
    root_key = key_match.group(1) if key_match else ""

    vars_match = re.search(r"\bvariables\s*=\s*\{", name_inner)
    if not vars_match:
        return _clean_loc_key(root_key) if root_key else "Unknown"

    vars_open_idx = vars_match.end() - 1
    vars_inner, _ = _extract_balanced_block(name_inner, vars_open_idx)

    parts: list[str] = []
    pos = 0
    while pos < len(vars_inner):
        brace_idx = vars_inner.find("{", pos)
        if brace_idx == -1:
            break
        var_block, next_pos = _extract_balanced_block(vars_inner, brace_idx)
        val_match = re.search(r"\bvalue\s*=\s*\{", var_block)
        if val_match:
            val_inner, _ = _extract_balanced_block(var_block, val_match.end() - 1)
            resolved_val = _resolve_name_block(val_inner)
            if resolved_val:
                parts.append(resolved_val)
        pos = next_pos

    if parts:
        if (
            root_key == "PLANET_NAME_FORMAT"
            and len(parts) == 2
            and len(parts[1]) == 1
            and parts[1].islower()
        ):
            return f"{parts[0]}{parts[1]}"
        return " ".join(parts)

    return _clean_loc_key(root_key) if root_key else "Unknown"


def extract_entity_name(block_text: str, default: str = "Unknown") -> str:
    """Extract the top-level `name` of a planet, system, or empire from its block."""
    struct_match = re.search(r"(?:^|\n)\s*name\s*=\s*\{", block_text)
    str_match = re.search(r'(?:^|\n)\s*name\s*=\s*"([^"]+)"', block_text)

    if struct_match and (not str_match or struct_match.start() < str_match.start()):
        inner, _ = _extract_balanced_block(block_text, struct_match.end() - 1)
        return _resolve_name_block(inner)
    if str_match:
        return _clean_loc_key(str_match.group(1))
    return default


def parse_meta(meta_text: str, save_path: Path | None = None) -> SaveMeta:
    """Parse empire name, in-game date, and version from `meta` text."""
    if not meta_text:
        return SaveMeta(save_path=save_path)

    name = extract_entity_name(meta_text, default="Unknown Empire")
    date_match = re.search(r'\bdate\s*=\s*"([^"]+)"', meta_text)
    version_match = re.search(
        r'\b(?:version_control_revision|version)\s*=\s*"([^"]+)"', meta_text
    )
    exact_ver_match = re.search(r'(?:^|\n)version\s*=\s*"([^"]+)"', meta_text)
    version = (
        exact_ver_match.group(1)
        if exact_ver_match
        else (version_match.group(1) if version_match else "Unknown Version")
    )
    return SaveMeta(
        empire_name=name,
        date=date_match.group(1) if date_match else "Unknown Date",
        version=version,
        save_path=save_path,
    )


def extract_modifiers(block_text: str, include_blockers: bool = False) -> list[str]:
    """Extract all modifier IDs (and optionally wildlife blockers) from an entity block."""
    modifiers: list[str] = []
    seen: set[str] = set()

    for tm_match in _TIMED_MODIFIER_RE.finditer(block_text):
        for mod_match in _MODIFIER_VALUE_RE.finditer(tm_match.group(1)):
            mod = mod_match.group(1)
            if mod not in seen:
                seen.add(mod)
                modifiers.append(mod)

    for mod_match in _MODIFIER_VALUE_RE.finditer(block_text):
        mod = mod_match.group(1)
        if mod not in seen:
            seen.add(mod)
            modifiers.append(mod)

    if include_blockers:
        for dep_match in _DEPOSIT_BLOCKER_RE.finditer(block_text):
            dep = dep_match.group(1)
            if dep not in seen:
                seen.add(dep)
                modifiers.append(dep)

    return modifiers


def parse_planet_block(
    planet_id: int, block_text: str, include_blockers: bool = False
) -> PlanetInfo:
    """Parse a single planet block into a `PlanetInfo`."""
    name = extract_entity_name(block_text, default=f"Planet #{planet_id}")
    pc_match = _PLANET_CLASS_RE.search(block_text)
    planet_class = pc_match.group(1) if pc_match else "unknown"

    size_match = _PLANET_SIZE_RE.search(block_text)
    planet_size = int(size_match.group(1)) if size_match else None

    system_id: int | None = None
    coord_match = _COORD_BLOCK_RE.search(block_text)
    if coord_match:
        origin_match = _ORIGIN_RE.search(coord_match.group(1))
        if origin_match:
            system_id = int(origin_match.group(1))

    owner_match = _OWNER_RE.search(block_text)
    controller_match = _CONTROLLER_RE.search(block_text)
    surveyed_by_match = _SURVEYED_BY_RE.search(block_text)
    surveyed_flag_match = _SURVEYED_FLAG_RE.search(block_text)

    surveyed_by = int(surveyed_by_match.group(1)) if surveyed_by_match else None
    surveyed = bool(
        surveyed_by is not None
        or (surveyed_flag_match and surveyed_flag_match.group(1) == "yes")
    )

    modifiers = extract_modifiers(block_text, include_blockers=include_blockers)

    return PlanetInfo(
        planet_id=planet_id,
        name=name,
        planet_class=planet_class,
        planet_size=planet_size,
        system_id=system_id,
        modifiers=modifiers,
        owner_id=int(owner_match.group(1)) if owner_match else None,
        controller_id=int(controller_match.group(1)) if controller_match else None,
        surveyed_by=surveyed_by,
        surveyed=surveyed,
    )


def parse_system_block(
    system_id: int, block_text: str, include_blockers: bool = False
) -> SystemInfo:
    """Parse a single `galactic_object` star system block into a `SystemInfo`."""
    name = extract_entity_name(block_text, default=f"System #{system_id}")
    sc_match = _STAR_CLASS_RE.search(block_text)
    star_class = sc_match.group(1) if sc_match else "unknown"

    x: float | None = None
    y: float | None = None
    coord_match = _COORD_BLOCK_RE.search(block_text)
    if coord_match:
        coord_inner = coord_match.group(1)
        xm = _FLOAT_X_RE.search(coord_inner)
        ym = _FLOAT_Y_RE.search(coord_inner)
        if xm:
            x = float(xm.group(1))
        if ym:
            y = float(ym.group(1))

    planet_ids: list[int] = []
    seen_pids: set[int] = set()
    for list_match in _SYSTEM_PLANETS_LIST_RE.finditer(block_text):
        for int_match in _INT_RE.finditer(list_match.group(1)):
            pid = int(int_match.group(1))
            if pid not in seen_pids:
                seen_pids.add(pid)
                planet_ids.append(pid)
    for single_match in _SYSTEM_PLANET_SINGLE_RE.finditer(block_text):
        pid = int(single_match.group(1))
        if pid not in seen_pids:
            seen_pids.add(pid)
            planet_ids.append(pid)

    owner_match = _OWNER_RE.search(block_text)
    controller_match = _CONTROLLER_RE.search(block_text)
    owner_id = (
        int(owner_match.group(1))
        if owner_match
        else (int(controller_match.group(1)) if controller_match else None)
    )

    surveyed_flag_match = _SURVEYED_FLAG_RE.search(block_text)
    surveyed_by_match = _SURVEYED_BY_RE.search(block_text)
    surveyed = bool(
        surveyed_by_match is not None
        or (surveyed_flag_match and surveyed_flag_match.group(1) == "yes")
    )

    modifiers = extract_modifiers(block_text, include_blockers=include_blockers)

    return SystemInfo(
        system_id=system_id,
        name=name,
        star_class=star_class,
        x=x,
        y=y,
        planet_ids=planet_ids,
        modifiers=modifiers,
        owner_id=owner_id,
        surveyed=surveyed,
    )


def parse_starbase_owners(gamestate: str) -> dict[int, int]:
    """Parse `starbase_mgr` to map `system_id -> owner_country_id`."""
    sb_mgr = extract_top_level_section(gamestate, "starbase_mgr")
    if not sb_mgr:
        return {}

    sub_match = re.search(r"\bstarbases\s*=\s*\{", sb_mgr)
    sb_inner = (
        _extract_balanced_block(sb_mgr, sub_match.end() - 1)[0]
        if sub_match
        else sb_mgr
    )

    system_owners: dict[int, int] = {}
    for _, block in iter_id_blocks(sb_inner):
        sys_m = _SYSTEM_ID_RE.search(block)
        own_m = _OWNER_RE.search(block)
        if sys_m and own_m:
            system_owners[int(sys_m.group(1))] = int(own_m.group(1))
    return system_owners


def parse_player_context(
    gamestate: str,
    planets: dict[int, PlanetInfo] | None = None,
    systems: dict[int, SystemInfo] | None = None,
) -> PlayerContext:
    """Extract the player's country ID, capital planet/system, and surveyed planet list."""
    player_section = extract_top_level_section(gamestate, "player")
    country_id = 0
    if player_section:
        cm = _PLAYER_COUNTRY_RE.search(player_section)
        if cm:
            country_id = int(cm.group(1))

    capital_planet_id: int | None = None
    starting_system_id: int | None = None
    surveyed_planet_ids: set[int] = set()

    country_section = extract_top_level_section(gamestate, "country")
    if country_section:
        for cid, block in iter_id_blocks(country_section):
            if cid == country_id:
                cap_m = _CAPITAL_RE.search(block)
                if cap_m:
                    capital_planet_id = int(cap_m.group(1))
                start_m = _STARTING_SYSTEM_RE.search(block)
                if start_m:
                    starting_system_id = int(start_m.group(1))
                for surv_m in _SURVEYED_LIST_RE.finditer(block):
                    for int_m in _INT_RE.finditer(surv_m.group(1)):
                        surveyed_planet_ids.add(int(int_m.group(1)))
                break

    ctx = PlayerContext(
        country_id=country_id,
        capital_planet_id=capital_planet_id,
        starting_system_id=starting_system_id,
        surveyed_planet_ids=surveyed_planet_ids,
    )

    if planets is not None and systems is not None:
        _enrich_context_and_entities(ctx, planets, systems, gamestate)

    return ctx


def _enrich_context_and_entities(
    ctx: PlayerContext,
    planets: dict[int, PlanetInfo],
    systems: dict[int, SystemInfo],
    gamestate: str,
) -> None:
    """Propagate ownership, survey status, and capital system between planets, systems, and player context."""
    planet_to_system: dict[int, int] = {}
    for sid, sys_info in systems.items():
        for pid in sys_info.planet_ids:
            planet_to_system[pid] = sid

    for pid, planet in planets.items():
        if planet.system_id is None and pid in planet_to_system:
            planet.system_id = planet_to_system[pid]
        elif planet.system_id is not None and planet.system_id in systems:
            if pid not in systems[planet.system_id].planet_ids:
                systems[planet.system_id].planet_ids.append(pid)

    starbase_owners = parse_starbase_owners(gamestate)

    for sid, sys_info in systems.items():
        # 1. Determine system ownership from starbase_mgr, system block, or owned planets in system
        if sid in starbase_owners:
            sys_info.owner_id = starbase_owners[sid]
        elif sys_info.owner_id is None:
            for pid in sys_info.planet_ids:
                p = planets.get(pid)
                if p is not None and (p.owner_id is not None or p.controller_id is not None):
                    sys_info.owner_id = (
                        p.owner_id if p.owner_id is not None else p.controller_id
                    )
                    break

        sys_info.is_owned_by_player = (
            sys_info.owner_id is not None and sys_info.owner_id == ctx.country_id
        )

        # 2. Determine survey status for planets and the system
        any_planet_surveyed = False
        for pid in sys_info.planet_ids:
            p = planets.get(pid)
            if p is None:
                continue
            if (
                pid in ctx.surveyed_planet_ids
                or sys_info.is_owned_by_player
                or p.owner_id == ctx.country_id
            ):
                p.surveyed = True
            if p.surveyed:
                any_planet_surveyed = True

        if sys_info.is_owned_by_player or any_planet_surveyed:
            sys_info.surveyed = True

        if sys_info.surveyed:
            for pid in sys_info.planet_ids:
                p = planets.get(pid)
                if p is not None:
                    p.surveyed = True

    # 3. Resolve player's capital planet & homeworld system
    cap_sys: SystemInfo | None = None
    if ctx.capital_planet_id is not None and ctx.capital_planet_id in planets:
        cap_planet = planets[ctx.capital_planet_id]
        ctx.capital_planet_name = cap_planet.name
        if cap_planet.system_id is not None:
            cap_sys = systems.get(cap_planet.system_id)

    if cap_sys is None and ctx.starting_system_id is not None:
        cap_sys = systems.get(ctx.starting_system_id)

    if cap_sys is None:
        # Fallback: first planet owned by player country
        for p in planets.values():
            if p.owner_id == ctx.country_id:
                if ctx.capital_planet_name is None:
                    ctx.capital_planet_id = p.planet_id
                    ctx.capital_planet_name = p.name
                if p.system_id is not None and p.system_id in systems:
                    cap_sys = systems[p.system_id]
                    break

    if cap_sys is None:
        # Fallback: first system owned by player country
        for s in systems.values():
            if s.is_owned_by_player:
                cap_sys = s
                break

    ctx.capital_system = cap_sys


def parse_planets(
    gamestate: str, include_blockers: bool = False
) -> dict[int, PlanetInfo]:
    """Parse all planets from the `planets` (or legacy `planet`) section of `gamestate`."""
    planets_section = extract_top_level_section(gamestate, "planets")
    if planets_section is not None:
        sub_match = re.search(r"\bplanet\s*=\s*\{", planets_section)
        if sub_match:
            planets_inner, _ = _extract_balanced_block(
                planets_section, sub_match.end() - 1
            )
        else:
            planets_inner = planets_section
    else:
        planets_inner = extract_top_level_section(gamestate, "planet") or ""

    planets: dict[int, PlanetInfo] = {}
    for pid, block in iter_id_blocks(planets_inner):
        planets[pid] = parse_planet_block(pid, block, include_blockers=include_blockers)
    return planets


def parse_systems(
    gamestate: str, include_blockers: bool = False
) -> dict[int, SystemInfo]:
    """Parse all star systems from the `galactic_object` section of `gamestate`."""
    gal_section = extract_top_level_section(gamestate, "galactic_object") or ""
    systems: dict[int, SystemInfo] = {}
    for sid, block in iter_id_blocks(gal_section):
        systems[sid] = parse_system_block(sid, block, include_blockers=include_blockers)
    return systems


def find_systems_with_modifier(
    gamestate: str,
    modifier_pattern: str = DEFAULT_MODIFIER,
    *,
    include_blockers: bool = False,
    regex: bool = False,
) -> list[SystemWildlifeMatch]:
    """Find all star systems containing planets (or system modifiers) matching `modifier_pattern`."""
    matches, _, _ = analyze_save(
        gamestate,
        modifier_pattern=modifier_pattern,
        include_blockers=include_blockers,
        regex=regex,
    )
    return matches


def analyze_save(
    gamestate: str,
    modifier_pattern: str = DEFAULT_MODIFIER,
    *,
    include_blockers: bool = False,
    regex: bool = False,
) -> tuple[list[SystemWildlifeMatch], PlayerContext, dict[int, SystemInfo]]:
    """Parse `gamestate` and return `(matches, player_context, all_systems)`."""
    planets = parse_planets(gamestate, include_blockers=include_blockers)
    systems = parse_systems(gamestate, include_blockers=include_blockers)
    player_ctx = parse_player_context(gamestate, planets=planets, systems=systems)

    if regex:
        compiled = re.compile(modifier_pattern, re.IGNORECASE)
        matches_mod = lambda mod: bool(compiled.search(mod))
    else:
        target_lower = modifier_pattern.lower()
        targets = {target_lower}
        if include_blockers and target_lower == DEFAULT_MODIFIER:
            targets.add(DANGEROUS_WILDLIFE_BLOCKER)
        matches_mod = lambda mod: mod.lower() in targets or target_lower in mod.lower()

    system_planet_matches: dict[int, list[PlanetInfo]] = {}
    for planet in planets.values():
        if any(matches_mod(m) for m in planet.modifiers):
            sid = planet.system_id if planet.system_id is not None else -1
            planet.system_id = sid
            system_planet_matches.setdefault(sid, []).append(planet)

    results: list[SystemWildlifeMatch] = []
    all_matched_sids = set(system_planet_matches.keys())
    for sid, sys_info in systems.items():
        if any(matches_mod(m) for m in sys_info.modifiers):
            all_matched_sids.add(sid)

    for sid in sorted(all_matched_sids):
        sys_info = systems.get(
            sid,
            SystemInfo(
                system_id=sid,
                name="Unknown System" if sid == -1 else f"System #{sid}",
                star_class="unknown",
                x=None,
                y=None,
            ),
        )
        matched_sys_mods = [m for m in sys_info.modifiers if matches_mod(m)]
        matched_planets = sorted(
            system_planet_matches.get(sid, []), key=lambda p: p.planet_id
        )
        results.append(
            SystemWildlifeMatch(
                system=sys_info,
                matching_planets=matched_planets,
                matching_system_modifiers=matched_sys_mods,
            )
        )

    results.sort(key=lambda r: (r.system.name.lower(), r.system.system_id))
    return results, player_ctx, systems


def marker_for_index(idx: int) -> str:
    """Return a single-character ASCII map marker (`1`..`9`, `A`..`Z`, `*`) for 0-based `idx`."""
    if 0 <= idx < len(_MAP_MARKERS):
        return _MAP_MARKERS[idx]
    return "*"


def render_ascii_starmap(
    matches: Sequence[SystemWildlifeMatch],
    player_ctx: PlayerContext,
    all_systems: Iterable[SystemInfo] = (),
    *,
    width: int = 59,
    height: int = 21,
) -> str:
    """Render an ASCII star map showing Galactic Center, Homeworld Capital, and matched systems."""
    points_x: list[float] = [0.0]
    points_y: list[float] = [0.0]

    for s in all_systems:
        if s.x is not None and s.y is not None:
            points_x.append(s.x)
            points_y.append(s.y)

    cap = player_ctx.capital_system
    if cap is not None and cap.x is not None and cap.y is not None:
        points_x.append(cap.x)
        points_y.append(cap.y)

    for m in matches:
        if m.system.x is not None and m.system.y is not None:
            points_x.append(m.system.x)
            points_y.append(m.system.y)

    # Use symmetric bounds around Galactic Center (0, 0) so (0, 0) sits right in the middle
    max_abs_x = max(max(abs(x) for x in points_x), 50.0) * 1.08
    max_abs_y = max(max(abs(y) for y in points_y), 50.0) * 1.08

    grid: list[list[str]] = [[" " for _ in range(width)] for _ in range(height)]

    def to_cell(x: float, y: float) -> tuple[int, int]:
        # In Stellaris's galaxy map camera, +X is left and -X is right
        col = int(round(((-x + max_abs_x) / (2.0 * max_abs_x)) * (width - 1)))
        row = int(round(((y + max_abs_y) / (2.0 * max_abs_y)) * (height - 1)))
        col = max(0, min(width - 1, col))
        row = max(0, min(height - 1, row))
        return row, col

    # Crosshair axes through Galactic Center (0, 0)
    center_row, center_col = to_cell(0.0, 0.0)
    for c in range(width):
        grid[center_row][c] = "-"
    for r in range(height):
        grid[r][center_col] = "|"

    # Background stars
    for s in all_systems:
        if s.x is not None and s.y is not None:
            r, c = to_cell(s.x, s.y)
            if grid[r][c] in (" ", "-", "|"):
                grid[r][c] = "."

    # Galactic Center
    grid[center_row][center_col] = "+"

    # Matched dangerous wildlife systems
    legend_matches: list[str] = []
    for idx, match in enumerate(matches):
        marker = marker_for_index(idx)
        sys_info = match.system
        if sys_info.x is not None and sys_info.y is not None:
            r, c = to_cell(sys_info.x, sys_info.y)
            grid[r][c] = marker
            legend_matches.append(
                f"[{marker}] {sys_info.name} ({sys_info.x:.1f}, {sys_info.y:.1f})"
            )

    # Player Homeworld / Capital system
    cap_legend = "Not found"
    if cap is not None and cap.x is not None and cap.y is not None:
        r, c = to_cell(cap.x, cap.y)
        grid[r][c] = "@"
        cap_planet_suffix = (
            f" / {player_ctx.capital_planet_name}"
            if player_ctx.capital_planet_name
            and player_ctx.capital_planet_name != cap.name
            else ""
        )
        cap_legend = f"{cap.name}{cap_planet_suffix} ({cap.x:.1f}, {cap.y:.1f})"

    lines: list[str] = []
    top_label = f" Galactic Star Map (X: +{max_abs_x:.0f}..-{max_abs_x:.0f}, Y: {-max_abs_y:.0f}..+{max_abs_y:.0f}) "
    border_fill = max(0, width - len(top_label))
    left_pad = border_fill // 2
    right_pad = border_fill - left_pad
    lines.append("+" + ("-" * left_pad) + top_label + ("-" * right_pad) + "+")
    for row in grid:
        lines.append("|" + "".join(row) + "|")
    lines.append("+" + ("-" * width) + "+")
    lines.append(
        f"Legend:  @ = Capital ({cap_legend})   + = Galactic Center (0.0, 0.0)   . = Other System"
    )
    if legend_matches:
        lines.append("Targets: " + "   ".join(legend_matches))

    return "\n".join(lines)


def summarize_all_modifiers(
    gamestate: str, include_blockers: bool = False
) -> dict[str, int]:
    """Return a frequency map of all planet modifiers present in the save."""
    planets = parse_planets(gamestate, include_blockers=include_blockers)
    counts: dict[str, int] = {}
    for planet in planets.values():
        for mod in planet.modifiers:
            counts[mod] = counts.get(mod, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
