# stellaris-sluggy

A fast Python CLI tool (managed with `uv`) that parses Stellaris `.sav` archives or extracted `gamestate` files and finds all star systems containing planets with the `dangerous_wildlife` modifier (or any other modifier).

## Usage

Scan the most recent `.sav` file across both your local Stellaris `save games` folder (`~/Documents/Paradox Interactive/Stellaris/save games`) and your Steam Cloud save folder (`~/Library/Application Support/Steam/userdata/*/281990/remote/save games`) automatically:

```bash
uv run stellaris-sluggy
```

Scan a specific `.sav` file, `gamestate` file, or save directory:

```bash
uv run stellaris-sluggy "/path/to/ironman.sav"
```

### Options

- `-m, --modifier <MODIFIER>`: Modifier key or substring to search for (default: `dangerous_wildlife`).
- `-b, --include-blockers`: Also match planets with dangerous wildlife blocker deposits (`d_dangerous_wildlife_blocker`).
- `-r, --regex`: Treat `--modifier` as a case-insensitive regular expression.
- `--no-map`: Omit the ASCII star map from the output.
- `--list-modifiers`: List all distinct planet modifiers present in the save file with planet counts.
- `--json`: Output results as structured JSON (including ownership, survey status, and capital coordinates).

### Running Tests

```bash
uv run python -m unittest discover -s tests -v
```
