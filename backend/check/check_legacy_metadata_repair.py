from backend.temporary.build_legacy_import import (
    BuildLegacyImport,
    COMPETITION_TYPES,
    normalize_legacy_contract_end,
    ParsedTable,
    normalize_legacy_date,
    normalize_season_id,
)


def test_legacy_contract_dates_use_one_identity_format() -> None:
    assert normalize_legacy_date("2020.07.31") == "2020-07-31"
    assert normalize_legacy_date("20200731") == "2020-07-31"
    assert normalize_legacy_date("9999.99.99") == "9999-99-99"
    assert normalize_legacy_contract_end("9999.99.99") is None


def test_legacy_team_season_ids_match_match_season_ids() -> None:
    assert normalize_season_id("2020-21") == "20202021"
    assert normalize_season_id("2023-24") == "20232024"
    assert normalize_season_id("20232024") == "20232024"


def test_competition_registry_has_no_implicit_other_type() -> None:
    assert COMPETITION_TYPES["EPL"] == "league"
    assert COMPETITION_TYPES["UCL"] == "cup"
    assert COMPETITION_TYPES["WC2022"] == "national"
    assert COMPETITION_TYPES["TEST"] == "test"
    assert "OTHER" not in COMPETITION_TYPES.values()


def test_future_import_keeps_current_team_league_and_normalizes_contract_ids() -> None:
    tables = {
        "ff_team": ParsedTable(
            columns=["t_code", "t_name", "t_name_kr", "t_name_full", "t_name_short", "s_code", "l_code"],
            rows=[["E-LI", "Liverpool", "리버풀", "Liverpool FC", "LIV", "", "UCL"]],
        ),
        "ff_player": ParsedTable(columns=["p_id", "p_name"], rows=[["P-1", "Player"]]),
        "ff_player_team": ParsedTable(
            columns=["p_id", "t_code", "pt_begin", "pt_end", "pt_num", "pt_pos", "l_code"],
            rows=[
                ["P-1", "E-LI", "2020.07.31", "2020.09.08", "7", "MF", "EPL"],
                ["P-1", "ENG1", "2022.11.20", "2022.12.19", "4", "MF", "WC2022"],
            ],
        ),
        "ff_stadium": ParsedTable(
            columns=["s_code", "s_name", "s_name_kr", "s_seats", "s_country", "s_city", "s_hometeam", "s_ground"],
            rows=[["UK_Anfield", "Anfield", "", 0, "ENGLAND", "Liverpool", "Liverpool", "1"]],
        ),
    }
    build = object.__new__(BuildLegacyImport)
    items, _, _ = build._build_items(tables, [])
    by_path = {path: payload for path, payload, _ in items}

    assert "currentLeagueId" not in by_path["teams/E-LI"]
    assert "stadiumId" not in by_path["teams/E-LI"]
    assert by_path["stadiums/UK_Anfield"]["homeTeamId"] == "E-LI"
    assert "players/P-1/contracts/E-LI_2020-07-31" in by_path
    assert by_path["players/P-1/contracts/E-LI_2020-07-31"]["to"] == "2020-09-08"
    assert by_path["players/P-1/contracts/E-LI_2020-07-31"]["competitionType"] == "league"
    assert by_path["players/P-1/contracts/ENG1_2022-11-20"]["competitionType"] == "national"
