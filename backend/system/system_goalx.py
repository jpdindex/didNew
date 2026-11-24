from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta
from fractions import Fraction
from functools import lru_cache
from hashlib import sha256
from math import dist, isfinite
from pathlib import Path
from statistics import median
from typing import Literal
from uuid import uuid4

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.auth.exceptions import GoogleAuthError
from google.cloud import firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2.service_account import Credentials
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.system.system_firestore import (
    PROJECT_ROOT, BackendError, JpdDidData, NotFoundError,
    ensure_firebase_app, get_settings,
)
from backend.system.system_schema import Side

COLLECTION = "GoalAalysis"
TEST_LEAGUE = "TESTGOAL"
TEST_SEASON = "test"
TEST_ROUND = "unassigned"
TEST_GOAL_PREFIX = "testgoal-"
TEST_CLOCK = r"^(?:[0-9]{1,3}\+)?[0-9]{1,3}:[0-5][0-9]$"
POINTS = {"high": 3, "medium": 2, "low": 1}
METRICS = ("distance", "speed")
GOAL_RESULTS = ("GOAL", "G")  # RAW can still contain the legacy SQL result code.
HALF_OFFSETS = {"H1": 0, "H2": 45 * 60, "H3": 90 * 60, "H4": 105 * 60}
logger = logging.getLogger(__name__)
record_reads = ThreadPoolExecutor(max_workers=8, thread_name_prefix="goalx-records")
Grade = Literal["high", "medium", "low"]


def automatic_goal_x(record, recording, pos_x):
    half = record.get("half")
    field_side = recording.get("fieldSideEx" if half in ("H3", "H4") else "fieldSide")
    if field_side in ("left", "right") and half in ("H1", "H2", "H3", "H4"):
        own_on_left = (field_side == "left") == (half in ("H1", "H3"))
        return 105 if own_on_left else 0
    return 0 if pos_x is not None and pos_x < 50 else 105


class EstimatedShotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    posX: float = Field(ge=0, le=100, allow_inf_nan=False)
    posY: float = Field(ge=0, le=100, allow_inf_nan=False)
    shootPosX: float = Field(ge=83, le=464, allow_inf_nan=False)
    shootPosY: float = Field(ge=83, le=300, allow_inf_nan=False)
    goalX: Literal[0, 105]
    timeToGoal: float | None = Field(default=None, gt=0, le=600, allow_inf_nan=False)


def estimate_shot(value: EstimatedShotInput, grade: str):
    start = (value.posX * 1.05, value.posY * .68, 0)
    end = (value.goalX, 34 + (value.shootPosX - 83) / 381 * 7.32 - 3.66,
           (300 - value.shootPosY) / 217 * 2.44)
    # Peak lift above the straight segment = distance * fixed type coefficient.
    weight = {"high": .20, "medium": .10, "low": 0}[grade]
    lift = dist(start, end) * weight
    points = []
    for i in range(101):
        t = i / 100
        x, y, z = (a + (b - a) * t for a, b in zip(start, end))
        points.append({"x": x, "y": y, "z": z + 4 * lift * t * (1 - t)})
    length = sum(dist(tuple(a.values()), tuple(b.values())) for a, b in zip(points, points[1:]))
    return {"input": value.model_dump(), "modelVersion": 1, "trajectoryGrade": grade,
            "curveWeight": weight, "trajectory": points, "goalPoint": points[-1],
            "distance": length, "averageSpeed": length / value.timeToGoal * 3.6 if value.timeToGoal else None}


class RepresentativeApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourceVersion: str = Field(pattern=r"^[a-f0-9]{64}$")
    trajectory: Literal["high", "medium", "low"]
    coordinates: EstimatedShotInput | None = None


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def evaluation_version(analysis):
    return fingerprint({"evaluations": analysis.get("evaluations") or {}, "shotx": analysis.get("shotx") or {}})


def representative_proposal(analysis, record, recording, points):
    evaluations = analysis.get("evaluations") or {}
    if not evaluations:
        raise BackendError("평가가 있는 득점만 대표 승인할 수 있습니다.", 409, "goalx_no_evaluations")
    scores = {category: sum(points[entry["grades"][category]] for entry in evaluations.values()) / len(evaluations)
              for category in ("artistry", "defense", "trajectory")}
    counts = Counter(entry["grades"]["trajectory"] for entry in evaluations.values())
    modes = [grade for grade in ("high", "medium", "low") if counts[grade] == max(counts.values())]
    times = []
    for entry in evaluations.values():
        value = ((entry.get("estimatedShot") or {}).get("input") or {}).get("timeToGoal")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 600:
            times.append(value)
    coordinates = {key: record.get(key) for key in ("posX", "posY", "shootPosX", "shootPosY")}
    goal_x = automatic_goal_x(record, recording, coordinates["posX"])
    direction_side = recording.get("fieldSideEx" if record.get("half") in ("H3", "H4") else "fieldSide")
    direction_known = direction_side in ("left", "right") and record.get("half") in ("H1", "H2", "H3", "H4")
    return {"sourceVersion": fingerprint({"evaluationVersion": evaluation_version(analysis), "record": record,
                                          "recording": recording, "points": points}),
            "evaluationVersion": evaluation_version(analysis), "evaluationCount": len(evaluations),
            "categoryScores": scores, "score": sum(scores.values()), "trajectoryCounts": dict(counts),
            "trajectoryCandidates": modes, "timeToGoal": median(times) if times else None,
            "timeSampleCount": len(times), "coordinates": coordinates, "goalX": goal_x,
            "directionGoalX": goal_x if direction_known else None,
            "shotId": (analysis.get("shotx") or {}).get("id")}


def finalize_representative(analysis, record, recording, points, payload):
    proposal = representative_proposal(analysis, record, recording, points)
    if payload.sourceVersion != proposal["sourceVersion"]:
        raise BackendError("검토 중 평가 또는 RAW가 변경됐습니다. 창을 다시 열어 확인해 주세요.", 409, "goalx_review_changed")
    if payload.trajectory not in proposal["trajectoryCandidates"]:
        raise BackendError("최다 선택된 궤적 중 하나를 선택하세요.", 422, "goalx_invalid_representative_trajectory")
    estimation = None
    if payload.coordinates:
        value = payload.coordinates.model_copy(update={
            "goalX": automatic_goal_x(record, recording, payload.coordinates.posX), "timeToGoal": proposal["timeToGoal"],
        })
        estimation = estimate_shot(value, payload.trajectory)
    return {**proposal, "trajectory": payload.trajectory, "estimatedShot": estimation,
            "coordinates": payload.coordinates.model_dump(include={"posX", "posY", "shootPosX", "shootPosY"}) if payload.coordinates else None,
            "schemaVersion": 1}


class Grades(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artistry: Grade
    defense: Grade
    trajectory: Grade


class EvaluationInput(Grades):
    estimatedShot: EstimatedShotInput | None = None


class TestGoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    clock: str = Field(pattern=TEST_CLOCK)
    playerName: str = Field(min_length=1, max_length=120)
    home: str = Field(min_length=1, max_length=120)
    away: str = Field(min_length=1, max_length=120)
    scoringSide: Side = "H"
    date: date
    shotId: str | None = Field(default=None, min_length=1, max_length=500, pattern=r"^[^/]+$")

    @model_validator(mode="after")
    def distinct_teams(self):
        if self.home.casefold() == self.away.casefold():
            raise ValueError("홈팀과 원정팀은 서로 달라야 합니다.")
        return self


class Threshold(BaseModel):
    medium: float = Field(ge=0, allow_inf_nan=False)
    high: float = Field(ge=0, allow_inf_nan=False)

    def grade(self, value: float) -> Grade:
        if value >= self.high:
            return "high"
        return "medium" if value >= self.medium else "low"


def number(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value):
        return value
    return None


def shot_number(detail, index, key):
    value = number(detail.get(key))
    return value if value is not None else number(index.get(key))


def shot_point(value):
    if isinstance(value, dict) and all(number(value.get(axis)) is not None for axis in ("x", "y", "z")):
        return {axis: value[axis] for axis in ("x", "y", "z")}
    return None


def match_clock(half, seconds):
    minute, second = divmod(HALF_OFFSETS[half] + seconds, 60)
    return f"{minute:02}:{second:02}"


def match_round(match):
    if match.get("round") is not None:
        return str(match["round"])
    return match.get("stage") or "unassigned"


def team_info(team, team_id):
    names = [team.get(key) for key in ("nameKr", "name", "nameShort", "nameEn", "nameFull")]
    return {"name": next((name for name in names if name), team_id), "aliases": list(dict.fromkeys([team_id, *(name for name in names if name)]))}


def normalize(value):
    return "".join(char for char in unicodedata.normalize("NFKC", str(value or "")).casefold() if char.isalnum())


def clock_seconds(value):
    match = re.search(r"(?<!\d)(?:(\d{1,3})\+)?(\d{1,3}):([0-5]\d)(?!\d)", str(value or ""))
    return (int(match[1] or 0) + int(match[2])) * 60 + int(match[3]) if match else None


@lru_cache(maxsize=1)
def shotx_clients():
    settings = get_settings()
    path = settings.goalx_shotx_credentials_path or settings.aifootballx_credentials_path
    if path:
        credentials_path = Path(path).expanduser()
        if not credentials_path.is_absolute():
            credentials_path = PROJECT_ROOT / credentials_path
        credentials = Credentials.from_service_account_file(str(credentials_path))
    else:
        credentials = ensure_firebase_app().credential.get_credential()
    project = "aifootballx"
    return (
        firestore.Client(project=project, credentials=credentials),
        storage.Client(project=project, credentials=credentials).bucket(f"{project}.firebasestorage.app"),
    )


class GoalAnalysis:
    def __init__(self):
        self.did = JpdDidData()
        self.db = self.did.db
        self.analyses = self.db.collection(COLLECTION)

    def verify_analyst(self, uid):
        profile = self.db.collection("recorders").document(uid).get(timeout=10).to_dict()
        if not profile or profile.get("active") is False:
            raise BackendError("평가 또는 연결을 저장하려면 활성 분석관 계정이 필요합니다.", 403, "analyst_required")
        return profile.get("name") or uid

    @lru_cache(maxsize=4)
    def catalog(self, cache_window):
        leagues = self.db.collection("leagues").select(["name", "nameEn"])
        seasons = self.db.collection("seasons").select(["leagueId", "name", "from", "to"])
        league_future = record_reads.submit(lambda: list(leagues.stream(timeout=15)))
        season_rows = list(seasons.stream(timeout=15))
        league_rows = league_future.result()
        return {
            "leagues": [
                {"id": doc.id, "name": row.get("name") or row.get("nameEn") or doc.id}
                for doc in league_rows for row in [doc.to_dict() or {}]
                if doc.id != TEST_LEAGUE
            ] + [{"id": TEST_LEAGUE, "name": TEST_LEAGUE}],
            "seasons": [
                {"id": doc.id, "leagueId": row.get("leagueId"), "name": row.get("name") or doc.id, "from": row.get("from"), "to": row.get("to")}
                for doc in season_rows for row in [doc.to_dict() or {}] if row.get("leagueId") and row["leagueId"] != TEST_LEAGUE
            ] + [{"id": TEST_SEASON, "leagueId": TEST_LEAGUE, "name": "테스트", "from": None, "to": None}],
        }

    def _matches(self, league_id, season_id):
        return self.db.collection("matches").where(
            filter=FieldFilter("leagueId", "==", league_id)
        ).where(filter=FieldFilter("seasonId", "==", season_id))

    @lru_cache(maxsize=32)
    def fixture_seasons(self, league_id, cache_window):
        if league_id == TEST_LEAGUE:
            return [TEST_SEASON]
        query = self.db.collection("matches").where(filter=FieldFilter("leagueId", "==", league_id)).select(["seasonId"])
        return sorted({season for doc in query.stream(timeout=20) if (season := doc.to_dict().get("seasonId"))})

    @lru_cache(maxsize=64)
    def rounds(self, league_id, season_id, cache_window):
        if league_id == TEST_LEAGUE:
            return [{"id": TEST_ROUND, "latestDate": "", "earliestDate": ""}] if season_id == TEST_SEASON else []
        rounds = {}
        fixtures = self._matches(league_id, season_id).select(["round", "stage", "date"])
        for snapshot in fixtures.stream(timeout=20):
            match = snapshot.to_dict()
            key = match_round(match)
            row = rounds.setdefault(key, {"id": key, "latestDate": "", "earliestDate": ""})
            date = str(match.get("date") or "").replace("-", "").replace(".", "")
            if date:
                row["latestDate"] = max(row["latestDate"], date)
                row["earliestDate"] = min(row["earliestDate"] or date, date)
        return sorted(rounds.values(), key=lambda row: (row["latestDate"], row["id"]), reverse=True)

    def _goal_key(self, gm_id, side, record_id):
        return sha256(json.dumps([gm_id, side, record_id], ensure_ascii=False).encode()).hexdigest()

    def _goal_collection(self, season_id, league_id):
        return self.analyses.document(league_id).collection("seasons").document(season_id).collection("goals")

    def _previous_goal_collection(self, season_id, league_id):
        return self.analyses.document(season_id).collection("leagues").document(league_id).collection("goals")

    def _ref(self, gm_id, side, record_id, season_id=None, league_id=None):
        if season_id is None or league_id is None:
            if gm_id.startswith(TEST_GOAL_PREFIX) and record_id == gm_id and side == "H":
                season_id, league_id = TEST_SEASON, TEST_LEAGUE
            else:
                match = self.did.get_match(gm_id)
                season_id, league_id = match.seasonId, match.leagueId
        return self._goal_collection(season_id, league_id).document(self._goal_key(gm_id, side, record_id))

    def _write_scope(self, writer, ref):
        season = ref.parent.parent
        league = season.parent.parent
        writer.set(league, {"kind": "league", "leagueId": league.id}, merge=True)
        writer.set(season, {"seasonId": season.id, "leagueId": league.id}, merge=True)

    def _historical_refs(self, ref):
        season = ref.parent.parent
        league = season.parent.parent
        return [self.analyses.document(ref.id), self._previous_goal_collection(season.id, league.id).document(ref.id)]

    def _read_goal_analysis(self, ref):
        refs = [*self._historical_refs(ref), ref]
        rows = {doc.reference.path: doc.to_dict() for doc in self.db.get_all(refs, timeout=15) if doc.exists}
        merged = {}
        for source in refs:
            merged = self._merge_analysis(merged, rows.get(source.path)) or {}
        return merged

    def _legacy_rows(self):
        query = self.analyses.where(filter=FieldFilter("gmId", ">", ""))
        return {doc.id: doc.to_dict() for doc in query.stream(timeout=15)}

    def _collect_analyses(self, analyses, collection):
        for doc in collection.stream(timeout=20):
            analyses[doc.id] = self._merge_analysis(analyses.get(doc.id), doc.to_dict())

    @staticmethod
    def _merge_analysis(legacy, current):
        if not legacy or not current:
            return current or legacy
        merged = {**legacy, **current}
        evaluations = dict(legacy.get("evaluations") or {})
        for uid, entry in (current.get("evaluations") or {}).items():
            old_time = evaluations.get(uid, {}).get("updatedAt")
            new_time = entry.get("updatedAt")
            if not old_time or (new_time and new_time >= old_time):
                evaluations[uid] = entry
        if evaluations:
            merged["evaluations"] = evaluations
        old_time = legacy.get("shotxUpdatedAt") or (legacy.get("shotx") or {}).get("linkedAt")
        new_time = current.get("shotxUpdatedAt") or (current.get("shotx") or {}).get("linkedAt")
        if old_time and new_time and old_time > new_time:
            merged["shotx"] = legacy.get("shotx")
            merged["shotxUpdatedAt"] = old_time
        return merged

    def move_legacy_goals(self, ref, sources=None):
        sources = self._historical_refs(ref) if sources is None else sources

        @firestore.transactional
        def move(transaction):
            rows = {doc.reference.path: doc.to_dict() for doc in self.db.get_all(sources, transaction=transaction, timeout=15)
                    if doc.exists}
            if not rows:
                return False
            legacy = {}
            for source in sources:
                if source.path in rows:
                    legacy = self._merge_analysis(legacy, rows[source.path])
            current = ref.get(transaction=transaction).to_dict()
            self._write_scope(transaction, ref)
            transaction.set(ref, self._merge_analysis(legacy, current), merge=True)
            for source in sources:
                if source.path in rows:
                    transaction.delete(source)
            return True

        return move(self.db.transaction())

    def _record_ref(self, gm_id, side, record_id):
        return self.db.collection("matches").document(gm_id).collection("recordings").document(side).collection("records").document(record_id)

    def _read_goal(self, gm_id, side, record_id):
        if gm_id.startswith(TEST_GOAL_PREFIX) and gm_id == record_id and side == "H":
            return self._test_goal_document(gm_id)[1]["manualGoal"]
        record = self._record_ref(gm_id, side, record_id).get(timeout=10).to_dict()
        if not record or record.get("res") not in GOAL_RESULTS:
            raise NotFoundError("득점 기록을 찾을 수 없습니다.")
        return record

    def raw_coordinates(self, gm_id, side, record_id):
        """Read the selected goal directly, independently of list projections/ShotX."""
        record = self._read_goal(gm_id, side, record_id)
        coordinates = {key: number(record.get(key)) for key in ("posX", "posY", "shootPosX", "shootPosY")}
        recording = self._recording_direction(gm_id, side, record_id)
        # Null means the browser should infer the nearer goal from its editable shooting position.
        coordinates["goalX"] = automatic_goal_x(record, recording, coordinates["posX"]) if (
            recording.get("fieldSideEx" if record.get("half") in ("H3", "H4") else "fieldSide") in ("left", "right")
            and record.get("half") in ("H1", "H2", "H3", "H4")
        ) else None
        return coordinates

    def _recording_direction(self, gm_id, side, record_id):
        if gm_id.startswith(TEST_GOAL_PREFIX) and gm_id == record_id and side == "H":
            return {}
        return self._record_ref(gm_id, side, record_id).parent.parent.get(
            field_paths=["fieldSide", "fieldSideEx"], timeout=10,
        ).to_dict() or {}

    def _rules(self, config):
        points = config.get("points")
        if not isinstance(points, dict) or any(type(points.get(grade)) is not int or points[grade] < 0 for grade in POINTS):
            points = POINTS.copy()
        thresholds = {
            metric: Threshold.model_validate(config["thresholds"][metric])
            for metric in METRICS
            if isinstance(config.get("thresholds"), dict) and metric in config["thresholds"]
        }
        if any(rule.high <= rule.medium for rule in thresholds.values()):
            raise BackendError("GoalX 배점 설정에서 상 기준은 중 기준보다 높아야 합니다.")
        return points, thresholds

    def _goal_row(self, gm_id, match, side, record_id, record):
        return {
            "gmId": gm_id, "side": side, "recordId": record_id,
            "teamId": match["homeTeamId" if side == "H" else "awayTeamId"],
            "homeTeamId": match["homeTeamId"], "awayTeamId": match["awayTeamId"],
            "date": match["date"], "round": match_round(match), "seasonId": match["seasonId"],
            "leagueId": match["leagueId"],
            "half": record["half"], "halfSeconds": record["halfSeconds"],
            "clock": match_clock(record["half"], record["halfSeconds"]),
            "playerId": record.get("playerId"),
            "rawCoordinates": {key: number(record.get(key)) for key in ("posX", "posY", "shootPosX", "shootPosY")},
        }

    def _goal_records(self, snapshot, match, side):
        records = snapshot.reference.collection("recordings").document(side).collection("records")
        query = records.where(filter=FieldFilter("res", "in", list(GOAL_RESULTS))).select(["half", "halfSeconds", "playerId", "posX", "posY", "shootPosX", "shootPosY"])
        return [self._goal_row(snapshot.id, match, side, goal.id, goal.to_dict()) for goal in query.stream(timeout=15)]

    def _analysis_state(self, analysis, uid, points, thresholds):
        evaluations = analysis.get("evaluations") or {}
        link = analysis.get("shotx") or {}
        metrics = link.get("metrics") or {}
        grades = {}
        for metric, rule in thresholds.items():
            value = number(metrics.get(metric))
            if value is not None:
                grades[metric] = rule.grade(value)
        evaluation_count = len(evaluations)
        category_totals = {
            category: sum(points[entry["grades"][category]] for entry in evaluations.values())
            for category in Grades.model_fields
        }
        score = Fraction(sum(category_totals.values()), evaluation_count) if evaluation_count else None
        representative = analysis.get("representative")
        return {
            "representative": ({"status": "approved" if representative.get("evaluationVersion") == evaluation_version(analysis)
                                and representative.get("points") == points else "needs_review",
                                "approvedBy": representative.get("approvedBy"), "approvedAt": representative.get("approvedAt")}
                               if representative else None),
            "shotId": link.get("id"), "metrics": metrics, "autoGrades": grades,
            "myGrades": (evaluations.get(uid) or {}).get("grades"),
            "myEstimatedShot": (evaluations.get(uid) or {}).get("estimatedShot"),
            "evaluationCount": evaluation_count,
            "evaluators": [
                {"id": entry.get("analystUid") or key, "name": entry.get("analystName") or key}
                for key, entry in sorted(evaluations.items())
            ],
            "score": float(score) if score is not None else None,
            "categoryScores": {
                category: total / evaluation_count for category, total in category_totals.items()
            } if evaluation_count else {},
        }

    def list(self, league_id, season_id, round_id, uid):
        if league_id == TEST_LEAGUE:
            return self.test_goals(season_id, round_id, uid)
        fixtures = self._matches(league_id, season_id)
        if round_id.isdecimal():
            fixtures = fixtures.where(filter=FieldFilter("round", "==", int(round_id)))
        fixtures = fixtures.select(["round", "stage", "date", "homeTeamId", "awayTeamId", "seasonId", "leagueId"])
        jobs, team_ids = [], set()
        for snapshot in fixtures.stream(timeout=20):
            match = snapshot.to_dict()
            if match_round(match) != round_id:
                continue
            for side, field in (("H", "homeTeamId"), ("A", "awayTeamId")):
                team = match[field]
                team_ids.add(team)
                jobs.append(record_reads.submit(self._goal_records, snapshot, match, side))
        goals = [goal for job in jobs for goal in job.result()]
        return self._goals_payload(goals, team_ids, uid)

    def ranking(self, league_id, season_id, round_id, uid):
        if league_id == TEST_LEAGUE:
            return self.test_goals(season_id, round_id, uid, evaluated_only=True)
        # Start with evaluations so opening an accumulated ranking does not read every match log.
        if season_id == "all":
            seasons = self.analyses.document(league_id).collection("seasons")
            season_ids = [doc.id for doc in seasons.select([]).stream(timeout=15)]
            previous_seasons = self.analyses.where(filter=FieldFilter("kind", "==", "season"))
            previous_season_ids = [doc.id for doc in previous_seasons.select([]).stream(timeout=15)]
        else:
            season_ids = previous_season_ids = [season_id]
        analyses = self._legacy_rows()
        for scope in previous_season_ids:
            self._collect_analyses(analyses, self._previous_goal_collection(scope, league_id))
        for scope in season_ids:
            query = self._goal_collection(scope, league_id).select(["gmId", "side", "recordId", "evaluations", "shotx", "shotxUpdatedAt", "representative"])
            self._collect_analyses(analyses, query)
        evaluated = [row for row in analyses.values() if not row.get("manualGoal") and row.get("evaluations") and row.get("gmId")
                     and row.get("side") in ("H", "A") and row.get("recordId")]
        matches = self.db.collection("matches")
        match_refs = [matches.document(gm_id) for gm_id in {row["gmId"] for row in evaluated}]
        match_fields = ["leagueId", "seasonId", "round", "stage", "date", "homeTeamId", "awayTeamId"]
        fixtures = {}
        if match_refs:
            fixtures = {doc.id: doc.to_dict() for doc in self.db.get_all(match_refs, field_paths=match_fields, timeout=20)
                        if doc.exists}
        scoped = {}
        for row in evaluated:
            gm_id, side, record_id = row["gmId"], row["side"], row["recordId"]
            match = fixtures.get(gm_id)
            if not match or match["leagueId"] != league_id:
                continue
            if season_id != "all" and match["seasonId"] != season_id:
                continue
            if round_id != "all" and match_round(match) != round_id:
                continue
            ref = self._record_ref(gm_id, side, record_id)
            scoped[ref.path] = (ref, row)
        goals, stored, team_ids = [], {}, set()
        if scoped:
            refs = [ref for ref, _ in scoped.values()]
            fields = ["res", "half", "halfSeconds", "playerId", "posX", "posY", "shootPosX", "shootPosY"]
            for doc in self.db.get_all(refs, field_paths=fields, timeout=20):
                record = doc.to_dict()
                if not record or record.get("res") not in GOAL_RESULTS:
                    continue
                _, analysis = scoped[doc.reference.path]
                gm_id, side = analysis["gmId"], analysis["side"]
                goal = self._goal_row(gm_id, fixtures[gm_id], side, doc.id, record)
                goals.append(goal)
                team_ids.update((goal["homeTeamId"], goal["awayTeamId"]))
                stored[self._ref(gm_id, side, doc.id, goal["seasonId"], league_id).path] = analysis
        return self._goals_payload(goals, team_ids, uid, stored)

    def _goals_payload(self, goals, team_ids, uid, analyses=None):
        goal_refs = [self._ref(goal["gmId"], goal["side"], goal["recordId"], goal["seasonId"], goal["leagueId"]) for goal in goals]
        player_ids = {goal["playerId"] for goal in goals if goal["playerId"] not in {None, "", "OWN"}}
        players = self.db.collection("players")
        teams = self.db.collection("teams")
        settings = self.analyses.document("_settings")
        refs = [settings, *(players.document(pid) for pid in player_ids), *(teams.document(tid) for tid in team_ids)]
        if analyses is None:
            refs.extend(goal_refs)
        legacy = self._legacy_rows() if analyses is None and goals else {}
        if analyses is None and goals:
            scope = goals[0]
            self._collect_analyses(legacy, self._previous_goal_collection(scope["seasonId"], scope["leagueId"]))
        stored = {snapshot.reference.path: snapshot.to_dict() for snapshot in self.db.get_all(refs, timeout=20) if snapshot.exists}
        if analyses is not None:
            stored.update(analyses)
        points, thresholds = self._rules(stored.get(settings.path) or {})
        for goal, ref in zip(goals, goal_refs, strict=True):
            player = goal["playerId"]
            name = stored.get(players.document(player).path, {}).get("name") if player and player != "OWN" else None
            goal["playerName"] = "자책골" if player == "OWN" else name or player
            analysis = self._merge_analysis(legacy.get(ref.id), stored.get(ref.path)) or {}
            goal.update(self._analysis_state(analysis, uid, points, thresholds))
        return {
            "goals": sorted(goals, key=lambda goal: (goal["date"], goal["gmId"], goal["half"], goal["halfSeconds"], goal["recordId"])),
            "points": points, "maxScore": max(points.values()) * len(Grades.model_fields), "automaticMetrics": list(thresholds),
            "teams": {tid: team_info(stored.get(teams.document(tid).path) or {}, tid) for tid in sorted(team_ids)},
        }

    def _read_analysis_state(self, ref, uid):
        settings = self.analyses.document("_settings")
        documents = {doc.id: doc.to_dict() for doc in self.db.get_all([ref, settings], timeout=15) if doc.exists}
        points, thresholds = self._rules(documents.get(settings.id) or {})
        return self._analysis_state(documents.get(ref.id) or {}, uid, points, thresholds)

    def _test_goal_document(self, goal_id):
        if not goal_id.startswith(TEST_GOAL_PREFIX):
            raise NotFoundError("TESTGOAL 득점 기록을 찾을 수 없습니다.")
        ref = self._ref(goal_id, "H", goal_id)
        row = self._read_goal_analysis(ref)
        if not row.get("manualGoal") or row.get("leagueId") != TEST_LEAGUE:
            raise NotFoundError("TESTGOAL 득점 기록을 찾을 수 없습니다.")
        return ref, row

    def test_goals(self, season_id, round_id, uid, *, evaluated_only=False):
        goals, teams = [], {}
        config = self.analyses.document("_settings").get(timeout=10).to_dict() or {}
        points, thresholds = self._rules(config)
        if season_id in (TEST_SEASON, "all") and round_id in (TEST_ROUND, "all"):
            analyses = self._legacy_rows()
            self._collect_analyses(analyses, self._previous_goal_collection(TEST_SEASON, TEST_LEAGUE))
            self._collect_analyses(analyses, self._goal_collection(TEST_SEASON, TEST_LEAGUE))
            for row in analyses.values():
                manual = row.get("manualGoal")
                if not manual or row.get("leagueId") != TEST_LEAGUE or (evaluated_only and not row.get("evaluations")):
                    continue
                home, away = manual["home"].casefold(), manual["away"].casefold()
                teams[home] = team_info({"name": manual["home"]}, home)
                teams[away] = team_info({"name": manual["away"]}, away)
                goals.append({
                    "gmId": row["gmId"], "side": "H", "recordId": row["recordId"],
                    "teamId": home if manual["scoringSide"] == "H" else away,
                    "homeTeamId": home, "awayTeamId": away,
                    "date": manual["date"], "clock": manual["clock"], "playerName": manual["playerName"],
                    "half": "H1", "round": TEST_ROUND, "seasonId": TEST_SEASON, "manual": True,
                    **self._analysis_state(row, uid, points, thresholds),
                })
        return {"goals": sorted(goals, key=lambda goal: (goal["date"], clock_seconds(goal["clock"]) or 0, goal["gmId"])),
                "teams": teams, "points": points, "maxScore": max(points.values()) * len(Grades.model_fields)}

    def write_test_goal(self, payload, uid, goal_id=None):
        self.verify_analyst(uid)
        creating = goal_id is None
        if creating:
            goal_id = TEST_GOAL_PREFIX + uuid4().hex
            ref = self._ref(goal_id, "H", goal_id)
            previous = {}
        else:
            ref, previous = self._test_goal_document(goal_id)
        updates = {"manualGoal": payload.model_dump(mode="json", exclude={"shotId"}),
                   "updatedBy": uid, "updatedAt": firestore.SERVER_TIMESTAMP}
        if payload.shotId != (previous.get("shotx") or {}).get("id"):
            updates["shotx"] = self._shot_link(payload.shotId, uid)
            updates["shotxUpdatedAt"] = firestore.SERVER_TIMESTAMP
        if creating:
            batch = self.db.batch()
            self._write_scope(batch, ref)
            batch.create(ref, {**updates, "gmId": goal_id, "side": "H", "recordId": goal_id,
                               "leagueId": TEST_LEAGUE, "createdBy": uid, "createdAt": firestore.SERVER_TIMESTAMP})
            batch.commit(timeout=15)
        else:
            self.move_legacy_goals(ref)
            ref.update(updates, timeout=15)
        return goal_id

    def save(self, gm_id, side, record_id, uid, grades):
        name = self.verify_analyst(uid)
        record = self._read_goal(gm_id, side, record_id)
        ref = self._ref(gm_id, side, record_id)
        self.move_legacy_goals(ref)
        batch = self.db.batch()
        self._write_scope(batch, ref)
        estimation_input = getattr(grades, "estimatedShot", None)
        if estimation_input:
            goal_x = automatic_goal_x(record, self._recording_direction(gm_id, side, record_id), estimation_input.posX)
            estimation_input = estimation_input.model_copy(update={"goalX": goal_x})
        estimation = estimate_shot(estimation_input, grades.trajectory) if estimation_input else None
        batch.set(ref, {
            "gmId": gm_id, "side": side, "recordId": record_id,
            "evaluations": {uid: {"analystUid": uid, "analystName": name,
                                  "grades": grades.model_dump(include=set(Grades.model_fields)),
                                  "estimatedShot": estimation, "updatedAt": firestore.SERVER_TIMESTAMP}},
        }, merge=True)
        batch.commit(timeout=15)
        return self._read_analysis_state(ref, uid)

    def _representative_source(self, gm_id, side, record_id, ref, transaction=None):
        if transaction:
            analysis = ref.get(transaction=transaction, timeout=15).to_dict() or {}
        else:
            analysis = self._read_goal_analysis(ref)
        manual = gm_id.startswith(TEST_GOAL_PREFIX) and gm_id == record_id and side == "H"
        if manual:
            record, recording = analysis.get("manualGoal") or {}, {}
        else:
            raw_ref = self._record_ref(gm_id, side, record_id)
            record = raw_ref.get(transaction=transaction, timeout=15).to_dict() or {}
            if record.get("res") not in GOAL_RESULTS:
                raise NotFoundError("득점 기록을 찾을 수 없습니다.")
            recording = raw_ref.parent.parent.get(transaction=transaction, field_paths=["fieldSide", "fieldSideEx"], timeout=15).to_dict() or {}
        config = self.analyses.document("_settings").get(transaction=transaction, timeout=15).to_dict() or {}
        points, _ = self._rules(config)
        return analysis, record, recording, points

    def representative_preview(self, gm_id, side, record_id):
        ref = self._ref(gm_id, side, record_id)
        return representative_proposal(*self._representative_source(gm_id, side, record_id, ref))

    def approve_representative(self, gm_id, side, record_id, user, payload):
        from backend.system.system_player_requests import require_manager
        require_manager(user)
        ref = self._ref(gm_id, side, record_id)
        self.move_legacy_goals(ref)

        @firestore.transactional
        def approve(transaction):
            source = self._representative_source(gm_id, side, record_id, ref, transaction)
            result = finalize_representative(*source, payload)
            result.update({"approvedBy": user.uid, "approvedAt": firestore.SERVER_TIMESTAMP, "points": source[3],
                           "gmId": gm_id, "side": side, "recordId": record_id,
                           "leagueId": ref.parent.parent.parent.parent.id, "seasonId": ref.parent.parent.id})
            transaction.update(ref, {"representative": result})

        approve(self.db.transaction())
        return self._read_analysis_state(ref, user.uid)

    def export_completed(self, league_id, season_id, round_id, uid):
        goals = [goal for goal in self.list(league_id, season_id, round_id, uid)["goals"] if goal["evaluationCount"]]
        refs = [self._ref(goal["gmId"], goal["side"], goal["recordId"], season_id, league_id) for goal in goals]
        sources = [source for ref in refs for source in [*self._historical_refs(ref), ref]]
        stored = {}
        for offset in range(0, len(sources), 200):
            stored.update({doc.reference.path: doc.to_dict() for doc in self.db.get_all(sources[offset:offset + 200], timeout=20) if doc.exists})
        output = []
        for goal, ref in zip(goals, refs, strict=True):
            analysis = {}
            for source in [*self._historical_refs(ref), ref]:
                analysis = self._merge_analysis(analysis, stored.get(source.path)) or {}
            output.append({"gmId": goal["gmId"], "side": goal["side"], "recordId": goal["recordId"],
                           "evaluations": [{"id": analyst, **entry} for analyst, entry in sorted((analysis.get("evaluations") or {}).items())],
                           "representative": analysis.get("representative"),
                           "representativeStatus": (goal.get("representative") or {}).get("status")})
        return output

    def _shot_document(self, shot_id):
        db, bucket = shotx_clients()
        shot = db.collection("shot_x").document(shot_id).get(timeout=10).to_dict()
        if not shot or shot.get("is_active") is False or shot.get("is_goal") is not True:
            raise NotFoundError("연결할 수 있는 ShotX 득점 결과를 찾을 수 없습니다.")
        return shot, bucket

    def candidates(self, gm_id, side, record_id, search):
        record = self._read_goal(gm_id, side, record_id)
        match = self.did.get_match(gm_id)
        teams = []
        for team_id in (match.homeTeamId, match.awayTeamId):
            team = self.db.collection("teams").document(team_id).get(timeout=10).to_dict() or {}
            teams.append({normalize(value) for value in [team_id, *(team.get(key) for key in ("name", "nameKr", "nameEn", "nameFull", "nameShort"))] if value})

        clock = HALF_OFFSETS[record["half"]] + record["halfSeconds"]
        return self.shot_candidates(search, teams, clock)

    def shot_candidates(self, search, teams=None, clock=None):
        db, _ = shotx_clients()
        query = db.collection("shot_x").where(filter=FieldFilter("is_goal", "==", True))
        rows = []
        for snapshot in query.stream(timeout=20):
            shot = snapshot.to_dict()
            if shot.get("is_active") is False:
                continue
            board = shot.get("scoreboard") or {}
            home = (board.get("home") or {}).get("text")
            away = (board.get("away") or {}).get("text")
            shot_clock = board.get("clock")
            title = shot.get("dataset") or shot.get("filename") or snapshot.id
            searchable = normalize(" ".join(str(value or "") for value in (title, snapshot.id, home, away, shot_clock)))
            if normalize(search) not in searchable:
                continue
            same_teams = int(normalize(home) in teams[0]) + int(normalize(away) in teams[1]) if teams else 0
            seconds = clock_seconds(shot_clock)
            match_date = shot.get("match_date") or board.get("match_date")
            if isinstance(match_date, str):
                try:
                    match_date = date.fromisoformat(match_date).isoformat()
                except ValueError:
                    match_date = None
            scorer = shot.get("scorer") or board.get("scorer")
            rows.append((
                (-same_teams, abs(clock - seconds) if clock is not None and seconds is not None else float("inf"), str(title), snapshot.id),
                {"id": snapshot.id, "title": title, "home": home, "away": away, "clock": shot_clock,
                 "date": match_date if isinstance(match_date, str) else None,
                 "playerName": scorer if isinstance(scorer, str) else None},
            ))
        return [row for _, row in sorted(rows)[:50]]

    def _measurement(self, shot_id):
        shot, bucket = self._shot_document(shot_id)
        path = shot.get("json_storage_path")
        detail = {}
        if path and path.startswith("shot_x/results/"):
            try:
                detail = json.loads(bucket.blob(path).download_as_bytes(timeout=15))
            except NotFound:
                logger.warning("ShotX result JSON is missing for %s", shot_id)
        metrics = {
            "distance": number(detail.get("straight_planar_distance")),
            "speed": shot_number(detail, shot, "initial_speed_kmh"),
            "finalSpeed": shot_number(detail, shot, "final_speed_kmh"),
            "launchAngle": shot_number(detail, shot, "launch_angle_deg"),
            "spin": shot_number(detail, shot, "omega_rpm"),
            "flightTime": number(detail.get("flight_time")),
            "timeToGoal": number(detail.get("time_to_goal")),
        }
        return shot, bucket, detail, metrics

    def _shot_link(self, shot_id, uid):
        if not shot_id:
            return None
        _, _, _, metrics = self._measurement(shot_id)
        return {"id": shot_id, "metrics": metrics, "linkedBy": uid, "linkedAt": firestore.SERVER_TIMESTAMP}

    def link(self, gm_id, side, record_id, uid, shot_id):
        self.verify_analyst(uid)
        self._read_goal(gm_id, side, record_id)
        ref = self._ref(gm_id, side, record_id)
        link = self._shot_link(shot_id, uid)
        self.move_legacy_goals(ref)
        # Replace only the link so concurrent analysts can save evaluations independently.
        batch = self.db.batch()
        self._write_scope(batch, ref)
        batch.set(ref, {"gmId": gm_id, "side": side, "recordId": record_id, "shotx": link, "shotxUpdatedAt": firestore.SERVER_TIMESTAMP},
                  merge=["gmId", "side", "recordId", "shotx", "shotxUpdatedAt"])
        batch.commit(timeout=15)
        return self._read_analysis_state(ref, uid)

    def detail(self, gm_id, side, record_id):
        self._read_goal(gm_id, side, record_id)
        ref = self._ref(gm_id, side, record_id)
        analysis = self._read_goal_analysis(ref)
        shot_id = (analysis.get("shotx") or {}).get("id")
        if not shot_id:
            return None
        shot, bucket, detail, metrics = self._measurement(shot_id)
        video_url = None
        path = shot.get("video_storage_path")
        if path and path.startswith("shot_x/videos/"):
            try:
                video_url = bucket.blob(path).generate_signed_url(version="v4", expiration=timedelta(hours=1))
            except (AttributeError, ValueError, GoogleAuthError):
                logger.warning("GoalX video signing is unavailable", exc_info=True)

        goal = detail.get("goal_result") or shot.get("goal_result") or {}
        board = detail.get("scoreboard") or shot.get("scoreboard") or {}
        return {
            "id": shot_id, "title": shot.get("dataset") or shot.get("filename") or shot_id,
            "metrics": metrics,
            "trajectory": [point for raw in detail.get("trajectory") or [] if (point := shot_point(raw))],
            "measuredTrajectory": [point for raw in detail.get("measured_trajectory") or [] if (point := shot_point(raw))],
            "goalPoint": shot_point(goal.get("crossing_point")),
            "goalX": number(goal.get("goal_x")),
            "goalCenterY": number(goal.get("goal_center_y")),
            "scoreboard": {"home": (board.get("home") or {}).get("text"), "away": (board.get("away") or {}).get("text"), "clock": board.get("clock")},
            "videoUrl": video_url,
        }


@contextmanager
def shotx_access():
    try:
        yield
    except (GoogleAPICallError, GoogleAuthError, OSError, ValueError) as error:
        logger.warning("ShotX lookup failed: %s", type(error).__name__)
        raise BackendError("ShotX 데이터를 읽을 수 없습니다. 연결 설정과 저장된 결과를 확인해 주세요.", 503, "shotx_unavailable") from error
