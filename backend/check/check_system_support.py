from backend.system.system_support import KpiRecord, calculate_kpis, calculate_team_kpi_5min, compute_attack_paths, compute_bap


def test_kpi_engine_builds_team_player_and_twenty_rating_snapshots() -> None:
    records = [
        KpiRecord("r1", "H1", 10, 1, "P", "O", 12, "p1"),
        KpiRecord("r2", "H1", 12, 2, "S", "GOAL", 4, "p2", is_shot=True),
        KpiRecord("r3", "H2", 20, 1, "S", "GOAL", 5, "p3", is_shot=True),
    ]

    result = calculate_kpis(records)

    assert result.team_kpi["TAP"] == 3
    assert result.team_kpi["DTP"] == 2
    assert result.team_kpi["GOAL"] == 2
    assert result.player_kpis["p2"]["GOAL"] == 1
    snapshots = calculate_team_kpi_5min(records)
    assert len(snapshots) == 20
    assert snapshots[-1]["GOAL"] == result.team_kpi["GOAL"]


def test_kpi_engine_does_not_join_attack_paths_across_halves() -> None:
    result = calculate_kpis([
        KpiRecord("h1", "H1", 100, 1, "P", "O", 5, "p1"),
        KpiRecord("h2", "H2", 1, 1, "P", "O", 5, "p2"),
    ])

    assert len(result.paths) == 2
    assert {path.id for path in result.paths} == {"h1_path_1", "h2_path_1"}


def test_bap_uses_legacy_server_batch_action_normalization() -> None:
    records = [
        KpiRecord("r1", "H1", 1, 1, "P", "O", 12),
        KpiRecord("r2", "H1", 2, 2, "P", "B", 8),
    ]

    assert compute_bap(records) == []


def test_server_batch_keeps_blank_result_action_records_in_the_path() -> None:
    records = [
        KpiRecord("action", "H1", 1, 1, "P", "", 12, "p1"),
        KpiRecord("result", "H1", 2, 2, "", "X", 8),
    ]

    paths, flags = compute_attack_paths(records)

    assert paths[0].record_ids == ("action", "result")
    assert flags["action"].is_tap is True
