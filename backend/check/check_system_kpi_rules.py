from backend.system.system_kpi_rules import KpiRecord, calculate_kpis, calculate_team_kpi_5min, compute_attack_paths, compute_bap
from backend.build.build_match_kpis import _is_legacy_baseline_drift, _team_differences
from backend.system.system_schema import RecordingKpi


def test_kpi_engine_builds_team_player_and_twenty_rating_snapshots() -> None:
    records = [
        KpiRecord("r1", "H1", 10, 1, "P", "O", 12, "p1"),
        KpiRecord("r2", "H1", 12, 2, "S", "GOAL", 4, "p2", is_shot=True),
        KpiRecord("r3", "H2", 20, 1, "S", "GOAL", 5, "p3", is_shot=True),
    ]

    result = calculate_kpis(records)

    assert result.team_kpi["TAP"] == 3
    # PHP gi_ctp counts CTP only; an isolated H2 shot is STP, not DTP.
    assert result.team_kpi["DTP"] == 1
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


def test_last_five_minute_snapshot_includes_the_recording_half_end() -> None:
    records = [
        KpiRecord("early", "H2", 3000, 1, "S", "GOAL", 5, "p1", is_shot=True),
        KpiRecord("added", "H2", 3186, 2, "S", "GOAL", 5, "p1", is_shot=True),
    ]

    snapshots = calculate_team_kpi_5min(records, {"H1": 3000, "H2": 3186})

    assert snapshots[-1]["GOAL"] == 2


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


def test_legacy_path_cache_drift_is_limited_to_one_count_path_fields() -> None:
    assert _is_legacy_baseline_drift(
        {"TTP": {"expected": 53, "calculated": 52}},
        {"player": {"UTP": {"expected": 22, "calculated": 21}}},
    )
    assert not _is_legacy_baseline_drift(
        {"TTP": {"expected": 53, "calculated": 50}},
        {"player": {"UTP": {"expected": 22, "calculated": 19}}},
    )
    assert not _is_legacy_baseline_drift(
        {"GOAL": {"expected": 2, "calculated": 1}},
        {"player": {"GOAL": {"expected": 2, "calculated": 1}}},
    )
    assert _is_legacy_baseline_drift(
        {"DTB": {"expected": 99, "calculated": 103}, "DTA": {"expected": 23, "calculated": 24}},
        {
            "p1": {"DTB": {"expected": 1, "calculated": 2}},
            "p2": {"DTB": {"expected": 1, "calculated": 2}},
            "p3": {"DTB": {"expected": 1, "calculated": 2}},
            "p4": {"DTB": {"expected": 1, "calculated": 2}, "DTA": {"expected": 1, "calculated": 2}},
        },
    )


def test_own_goal_is_not_a_team_shot_but_is_tracked_as_og() -> None:
    result = calculate_kpis([
        KpiRecord("goal", "H1", 10, 0, "S", "GOAL", 4, "scorer"),
        KpiRecord("own", "H1", 20, 0, "S", "GOAL", 4, "OWN"),
    ])

    assert result.team_kpi["GOAL"] == 2
    assert result.team_kpi["SHOT"] == 1
    assert result.team_kpi["TAP"] == 1
    assert result.team_kpi["OG"] == 1
    assert result.team_kpi["SSR"] == 1


def test_legacy_validation_does_not_compare_own_goal_placeholder() -> None:
    expected = RecordingKpi(**{field: 0 for field in RecordingKpi.model_fields})
    actual = expected.model_copy(update={"OG": 1})

    assert _team_differences(expected, actual) == {}
