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
    assert result.team_kpi["DTS"] == 1
    assert result.team_kpi["GOAL"] == 2
    assert result.player_kpis["p2"]["GOAL"] == 1
    assert result.player_kpis["p2"]["DTS"] == 1
    assert result.player_kpis["p3"]["DTS"] == 0
    snapshots = calculate_team_kpi_5min(records)
    assert len(snapshots) == 20
    assert snapshots[-1]["GOAL"] == result.team_kpi["GOAL"]


def test_free_kick_r_never_links_to_preceding_actions() -> None:
    # R (free kick / PK) is a set piece: C/P/K/F before it do not make it DTP.
    paths, _ = compute_attack_paths([
        KpiRecord("p", "H1", 10, 1, "P", "O", 5, "p1"),
        KpiRecord("r", "H1", 11, 2, "R", "GOAL", 4, "p2", is_shot=True),
    ])
    assert [(path.record_ids, path.path_type) for path in paths] == [(("p",), "UPP"), (("r",), "STP")]

    # F followed by S (or H) is still one linked path → DTP.
    paths, _ = compute_attack_paths([
        KpiRecord("f", "H1", 10, 1, "F", "O", 8, "p1"),
        KpiRecord("s", "H1", 11, 2, "S", "GOAL", 4, "p2", is_shot=True),
    ])
    assert [path.path_type for path in paths] == ["DTP"]


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


def test_only_shots_linked_from_a_connect_act_are_dtp() -> None:
    linked, _ = compute_attack_paths([
        KpiRecord("p", "H1", 1, 1, "P", "O", 8, "p1"),
        KpiRecord("s", "H1", 3, 2, "S", "B", 4, "p2", is_shot=True),
    ])
    shot_to_shot, _ = compute_attack_paths([
        KpiRecord("s1", "H1", 1, 1, "S", "O", 4, "p1", is_shot=True),
        KpiRecord("s2", "H1", 3, 2, "S", "X", 4, "p2", is_shot=True),
    ])

    assert [path.path_type for path in linked] == ["DTP"]
    assert [path.path_type for path in shot_to_shot] == ["STP"]


def test_dts_counts_only_the_final_shot_of_each_dtp() -> None:
    records = []
    for index in range(14):
        start = index * 10
        records.extend([
            KpiRecord(f"pass-{index}", "H1", start, 0, "P", "O", 8, "passer"),
            KpiRecord(f"shot-{index}", "H1", start + 1, 0, "S", "", 4, "shooter" if index else None),
            KpiRecord(f"result-{index}", "H1", start + 2, 0, "", "X", 4),
        ])
    records.extend(KpiRecord(f"solo-{index}", "H2", index * 10, 0, "S", "X", 4, "solo") for index in range(5))

    result = calculate_kpis(records)

    assert result.team_kpi["DTP"] == result.team_kpi["DTS"] == 14
    assert result.team_kpi["SHOT"] == 19
    assert result.flags["shot-0"].is_dts
    assert not result.flags["result-0"].is_dts
    assert result.player_kpis["shooter"]["DTS"] == 13
    assert result.player_kpis["solo"]["DTS"] == 0


def test_dtp_requires_a_shot_as_the_last_action() -> None:
    result = calculate_kpis([
        KpiRecord("pass", "H1", 1, 0, "P", "O", 8, "p1"),
        KpiRecord("shot", "H1", 2, 0, "S", "O", 4, "p2"),
        KpiRecord("following", "H1", 3, 0, "C", "O", 4, "p3"),
    ])

    assert result.team_kpi["DTP"] == result.team_kpi["DTS"] == 0
    assert result.team_kpi["SHOT"] == 1


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


def test_legacy_records_use_persisted_sql_flags_and_path_identity() -> None:
    records = [
        KpiRecord("r1", "H1", 1, 0, "P", "O", 12, "p1", legacy_path_id="path-1", legacy_path_type="UTP", legacy_path_ttp=True, legacy_flags={"tap": True, "tapSuccess": True, "dap": True, "dapSuccess": True, "ast": False, "shot": False, "shotSuccess": False, "goal": False, "dtb": False, "dtm": False, "dta": False, "dts": False, "gtb": False, "gtm": False}),
        KpiRecord("r2", "H1", 2, 0, "S", "GOAL", 4, "p2", legacy_path_id="path-1", legacy_path_type="UTP", legacy_path_ttp=True, legacy_flags={"tap": True, "tapSuccess": True, "dap": True, "dapSuccess": True, "ast": True, "shot": True, "shotSuccess": True, "goal": True, "dtb": True, "dtm": False, "dta": True, "dts": True, "gtb": True, "gtm": False}),
    ]
    result = calculate_kpis(records)
    assert result.team_kpi["TTP"] == 1
    assert result.team_kpi["DTP"] == 0
    assert result.team_kpi["DTS"] == 0
    assert result.team_kpi["TAP"] == 2
    assert result.team_kpi["GOAL"] == 1
    assert result.player_kpis["p2"]["UTP"] == 1
    assert result.player_kpis["p2"]["AST"] == 1


def test_legacy_aggregate_only_path_counts_for_team_but_not_players() -> None:
    result = calculate_kpis([
        KpiRecord("raw", "H1", 1, 0, "P", "O", 12, "p1", legacy_path_id="raw-path", legacy_path_type="UPP", legacy_path_ttp=False, legacy_flags={"tap": True, "tapSuccess": True, "dap": True, "dapSuccess": True}),
        KpiRecord("legacy-path:empty", "H4", 0, 0, "", "", 18, legacy_path_id="empty-path", legacy_path_type="DTP", legacy_path_ttp=True, legacy_flags={}),
    ])

    assert result.team_kpi["TTP"] == 1
    assert result.team_kpi["DTP"] == 1
    assert result.team_kpi["DTS"] == 1
    assert result.player_kpis["p1"]["TTP"] == 0
    assert result.player_kpis["p1"]["DTP"] == 0
