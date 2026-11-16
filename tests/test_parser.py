"""Unit tests for stellaris-sluggy."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from stellaris_sluggy.cli import main
from stellaris_sluggy.parser import (
    analyze_save,
    find_latest_save,
    find_systems_with_modifier,
    get_steam_cloud_save_dirs,
    read_save_contents,
    render_ascii_starmap,
    summarize_all_modifiers,
)


SAMPLE_META = """\
version="v3.14.159"
name=
{
\tkey="United Nations of Earth"
}
date="2235.04.12"
"""

SAMPLE_GAMESTATE = """\
version="v3.14.159"
date="2235.04.12"
player=
{
\t{
\t\tname="Player"
\t\tcountry=0
\t}
}
country=
{
\t0=
\t{
\t\tname="United Nations of Earth"
\t\tcapital=1
\t\tstarting_system=0
\t\tsurveyed=
\t\t{
\t\t\t0 1 2
\t\t}
\t}
}
starbase_mgr=
{
\tstarbases=
\t{
\t\t0=
\t\t{
\t\t\tsystem=0
\t\t\towner=0
\t\t}
\t\t1=
\t\t{
\t\t\tsystem=1
\t\t\towner=0
\t\t}
\t}
}
planets=
{
\tplanet=
\t{
\t\t0=
\t\t{
\t\t\tname=
\t\t\t{
\t\t\t\tkey="Sol"
\t\t\t}
\t\t\tplanet_class="pc_g_star"
\t\t\tcoordinate=
\t\t\t{
\t\t\t\tx=10.5
\t\t\t\ty=-20.25
\t\t\t\torigin=0
\t\t\t}
\t\t\tplanet_size=30
\t\t}
\t\t1=
\t\t{
\t\t\tname="Earth"
\t\t\tplanet_class="pc_continental"
\t\t\tcoordinate=
\t\t\t{
\t\t\t\tx=12.5
\t\t\t\ty=-20.25
\t\t\t\torigin=0
\t\t\t}
\t\t\tplanet_size=18
\t\t\towner=0
\t\t\tcontroller=0
\t\t}
\t\t2=
\t\t{
\t\t\tname=
\t\t\t{
\t\t\t\tkey="PLANET_NAME_FORMAT"
\t\t\t\tvariables=
\t\t\t\t{
\t\t\t\t\t{
\t\t\t\t\t\tkey="1"
\t\t\t\t\t\tvalue=
\t\t\t\t\t\t{
\t\t\t\t\t\t\tkey="NAME_Alpha_Centauri"
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t\t{
\t\t\t\t\t\tkey="2"
\t\t\t\t\t\tvalue=
\t\t\t\t\t\t{
\t\t\t\t\t\t\tkey="ROMAN_III"
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t\tplanet_class="pc_tropical"
\t\t\tcoordinate=
\t\t\t{
\t\t\t\tx=42.0
\t\t\t\ty=15.0
\t\t\t\torigin=1
\t\t\t}
\t\t\tplanet_size=20
\t\t\tsurveyed_by=0
\t\t\tsurveyed=yes
\t\t\ttimed_modifier=
\t\t\t{
\t\t\t\tmodifier="dangerous_wildlife"
\t\t\t\tdays=-1
\t\t\t}
\t\t\ttimed_modifier=
\t\t\t{
\t\t\t\tmodifier="lush_planet"
\t\t\t\tdays=-1
\t\t\t}
\t\t}
\t\t3=
\t\t{
\t\t\tname=
\t\t\t{
\t\t\t\tkey="PLANET_NAME_FORMAT"
\t\t\t\tvariables=
\t\t\t\t{
\t\t\t\t\t{
\t\t\t\t\t\tkey="1"
\t\t\t\t\t\tvalue=
\t\t\t\t\t\t{
\t\t\t\t\t\t\tkey="PLANET_NAME_FORMAT"
\t\t\t\t\t\t\tvariables=
\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\t\tkey="1"
\t\t\t\t\t\t\t\t\tvalue=
\t\t\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\t\t\tkey="Sirius"
\t\t\t\t\t\t\t\t\t}
\t\t\t\t\t\t\t\t}
\t\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\t\tkey="2"
\t\t\t\t\t\t\t\t\tvalue=
\t\t\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\t\t\tkey="ROMAN_IV"
\t\t\t\t\t\t\t\t\t}
\t\t\t\t\t\t\t\t}
\t\t\t\t\t\t\t}
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t\t{
\t\t\t\t\t\tkey="2"
\t\t\t\t\t\tvalue=
\t\t\t\t\t\t{
\t\t\t\t\t\t\tkey="a"
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t\tplanet_class="pc_continental"
\t\t\tplanet_size=14
\t\t\ttimed_modifier=
\t\t\t{
\t\t\t\tmodifier="dangerous_wildlife"
\t\t\t\tdays=-1
\t\t\t}
\t\t}
\t}
}
galactic_object=
{
\t0=
\t{
\t\tcoordinate=
\t\t{
\t\t\tx=10.5
\t\t\ty=-20.25
\t\t\torigin=4294967295
\t\t}
\t\ttype=star
\t\tname=
\t\t{
\t\t\tkey="Sol"
\t\t}
\t\tplanet=0
\t\tplanet=1
\t\tstar_class="sc_g"
\t}
\t1=
\t{
\t\tcoordinate=
\t\t{
\t\t\tx=42.0
\t\t\ty=15.0
\t\t\torigin=4294967295
\t\t}
\t\ttype=star
\t\tname=
\t\t{
\t\t\tkey="NAME_Alpha_Centauri"
\t\t}
\t\tplanet={ 2 }
\t\tstar_class="sc_trinary_1"
\t}
\t2=
\t{
\t\tcoordinate=
\t\t{
\t\t\tx=-88.0
\t\t\ty=104.2
\t\t\torigin=4294967295
\t\t}
\t\ttype=star
\t\tname="Sirius"
\t\tplanet={ 3 }
\t\tstar_class="sc_a"
\t}
}
"""


class ParserTests(unittest.TestCase):
    def test_find_systems_with_dangerous_wildlife_and_context(self) -> None:
        matches, player_ctx, all_systems = analyze_save(SAMPLE_GAMESTATE)
        self.assertEqual(len(matches), 2)
        self.assertEqual(player_ctx.country_id, 0)
        self.assertIsNotNone(player_ctx.capital_system)
        assert player_ctx.capital_system is not None
        self.assertEqual(player_ctx.capital_system.name, "Sol")
        self.assertEqual(player_ctx.capital_planet_name, "Earth")

        alpha = matches[0]
        self.assertEqual(alpha.system.name, "Alpha Centauri")
        self.assertEqual(alpha.system.system_id, 1)
        self.assertEqual(alpha.system.x, 42.0)
        self.assertEqual(alpha.system.y, 15.0)
        self.assertTrue(alpha.system.is_owned_by_player)
        self.assertEqual(alpha.system.ownership_label, "Owned (You)")
        self.assertTrue(alpha.system.surveyed)
        self.assertEqual(len(alpha.matching_planets), 1)
        self.assertEqual(alpha.matching_planets[0].name, "Alpha Centauri III")
        self.assertEqual(alpha.matching_planets[0].planet_class, "pc_tropical")
        self.assertEqual(alpha.matching_planets[0].planet_size, 20)

        sirius = matches[1]
        self.assertEqual(sirius.system.name, "Sirius")
        self.assertEqual(sirius.system.system_id, 2)
        self.assertFalse(sirius.system.is_owned_by_player)
        self.assertEqual(sirius.system.ownership_label, "Unowned")
        self.assertFalse(sirius.system.surveyed)
        self.assertEqual(len(sirius.matching_planets), 1)
        self.assertEqual(sirius.matching_planets[0].name, "Sirius IVa")

        ascii_map = render_ascii_starmap(matches, player_ctx, all_systems.values())
        self.assertIn("@", ascii_map)
        self.assertIn("+", ascii_map)
        self.assertIn("1", ascii_map)
        self.assertIn("2", ascii_map)
        self.assertIn("Sol / Earth", ascii_map)
        # Alpha Centauri has x=+42.0 (left of center '|') and Sirius has x=-88.0 (right of center '|')
        line_with_1 = next(line for line in ascii_map.splitlines() if "1" in line and "|" in line)
        line_with_2 = next(line for line in ascii_map.splitlines() if "2" in line and "|" in line)
        self.assertLess(line_with_1.index("1"), line_with_1.index("|", 1))
        self.assertGreater(line_with_2.index("2"), line_with_2.index("|", 1))

    def test_sav_zip_and_cli_json_and_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sav_path = Path(tmpdir) / "ironman.sav"
            with zipfile.ZipFile(sav_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("meta", SAMPLE_META)
                zf.writestr("gamestate", SAMPLE_GAMESTATE)

            latest = find_latest_save([Path(tmpdir)])
            self.assertEqual(latest, sav_path)

            gamestate, meta = read_save_contents(sav_path)
            self.assertEqual(meta.empire_name, "United Nations of Earth")
            self.assertEqual(meta.date, "2235.04.12")
            self.assertEqual(meta.version, "v3.14.159")

            counts = summarize_all_modifiers(gamestate)
            self.assertEqual(counts["dangerous_wildlife"], 2)
            self.assertEqual(counts["lush_planet"], 1)

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main([str(sav_path), "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["system_count"], 2)
            self.assertEqual(payload["planet_count"], 2)
            self.assertEqual(payload["capital"]["system_name"], "Sol")
            self.assertEqual(payload["systems"][0]["system_name"], "Alpha Centauri")
            self.assertTrue(payload["systems"][0]["owned_by_player"])
            self.assertTrue(payload["systems"][0]["surveyed"])
            self.assertFalse(payload["systems"][1]["owned_by_player"])
            self.assertFalse(payload["systems"][1]["surveyed"])

            table_buf = io.StringIO()
            with redirect_stdout(table_buf):
                rc2 = main([str(sav_path)])
            self.assertEqual(rc2, 0)
            table_out = table_buf.getvalue()
            self.assertIn("Owned (You)", table_out)
            self.assertIn("Unowned", table_out)
            self.assertIn("Galactic Star Map", table_out)

    def test_steam_cloud_save_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            userdata_root = Path(tmpdir) / "Steam" / "userdata"
            cloud_save_dir = (
                userdata_root / "12345678" / "281990" / "remote" / "save games" / "une_1"
            )
            cloud_save_dir.mkdir(parents=True)
            cloud_sav = cloud_save_dir / "cloud_ironman.sav"
            cloud_sav.write_bytes(b"placeholder")

            discovered_dirs = get_steam_cloud_save_dirs([userdata_root])
            self.assertEqual(
                discovered_dirs,
                [userdata_root / "12345678" / "281990" / "remote" / "save games"],
            )

            latest = find_latest_save(
                search_dirs=discovered_dirs,
                steam_userdata_dirs=[userdata_root],
            )
            self.assertEqual(latest, cloud_sav)


if __name__ == "__main__":
    unittest.main()
