from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Canonical backend storage/domain contract.
# app/types/schema.ts is transitional frontend legacy and will be removed as API wiring progresses.
# New storage/domain rules belong here first; frontend keeps only UI/API-consumption types.

Half = Literal["H1", "H2", "H3", "H4"]
Side = Literal["H", "A"]
HalfStatus = Literal["ready", "H1", "H1_done", "H2", "H2_done", "final"]
InputMode = Literal["분석", "실시간"]
RecorderLevel = Literal["basic", "advanced"]
RecorderRank = Literal["main", "sub"]
DataSource = Literal["did", "vision"]
ActCode = Literal["C", "P", "K", "F", "S", "H", "R", ""]
ResCode = Literal["O", "X", "B", "GB", "GX", "GOAL", "L", "H", "R", "LX", "HX", "RX", ""]


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MatchScore(SchemaModel):
    home: int
    away: int


class MatchDoc(SchemaModel):
    date: str
    kickoffTime: str | None = None
    leagueId: str
    seasonId: str
    matchType: Literal["league", "tournament"]
    round: int | None = None
    stage: Literal["R32", "R16", "QF", "SF", "F"] | None = None
    group: str | None = None
    leg: Literal[1, 2] | None = None
    stadiumId: str
    homeTeamId: str
    awayTeamId: str
    score: MatchScore
    createdAt: datetime
    updatedAt: datetime


class HalfTiming(SchemaModel):
    startedAt: datetime | None
    seconds: int


class LineupEntry(SchemaModel):
    slot: str
    order: int
    type: Literal["START", "BENCH"]
    no: str
    name: str
    pos: str
    inHalf: Half | None
    inSeconds: int | None
    outHalf: Half | None
    outSeconds: int | None


class RecorderEntry(SchemaModel):
    rank: RecorderRank
    joinedAt: datetime


class RecordingKpi(SchemaModel):
    TAP: int | float
    DAP: int | float
    TTP: int | float
    DTP: int | float
    BAP: int | float
    DTB: int | float
    DTM: int | float
    DTA: int | float
    DTS: int | float
    SHOT: int | float
    ASR: int | float
    SSR: int | float
    GOAL: int | float
    OG: int | float


class TeamRatingSnapshot(SchemaModel):
    jmx: float
    jmxSeries: list[float]
    apx: float
    apxGrade: str
    tpx: float
    tpxGrade: str
    fpx: float
    fpxGrade: str


class RecordingDoc(SchemaModel):
    side: Side
    teamId: str
    opponentTeamId: str
    # RAW is the match-data source of truth. Analyst participation belongs to
    # operational Draft/audit data, so new RAW documents do not carry it.
    recorders: dict[str, RecorderEntry] = Field(default_factory=dict)
    recorderIds: list[str] = Field(default_factory=list)
    status: HalfStatus
    inputMode: InputMode
    fieldSide: Literal["left", "right"]
    fieldSideEx: Literal["left", "right"] | None = None
    formationKey: str
    lineup: dict[str, LineupEntry]
    halves: dict[Half, HalfTiming]
    h1Locked: bool
    h2Locked: bool
    maxSeq: int
    # KPI and rating are derived from this recording's raw records.
    kpi: RecordingKpi | None = None
    kpiComputedAt: datetime | None = None
    kpiVersion: int = 0
    kpiSourceFingerprint: str | None = None
    teamRating: TeamRatingSnapshot | None = None
    ratingBasedOn: int | None = None
    # Legacy imports use either the old numeric game ID or a Firestore-style ID.
    legacyGiId: int | str | None = None
    legacyRecorderId: str | None = None
    syncedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime


class RecordDoc(SchemaModel):
    half: Half
    halfSeconds: int
    seq: int
    act: ActCode
    res: ResCode
    area: Annotated[int, Field(ge=1, le=18)]
    posX: float | None = None
    posY: float | None = None
    shootPosX: float | None = None
    shootPosY: float | None = None
    shootDspRange: bool | None = None
    isShot: bool | None = None
    playerId: str | None = None
    # New RAW intentionally does not retain the operator UID.
    createdBy: str = ""
    playerIdBy: str | None = None
    source: DataSource
    playerIdSource: DataSource | None = None
    bapReason: str | None = None
    createdAt: datetime

    @field_validator("res", mode="before")
    @classmethod
    def normalize_legacy_goal(cls, value: str) -> str:
        return "GOAL" if value == "G" else value


class CardDoc(SchemaModel):
    playerId: str
    half: Half
    halfSeconds: int
    card: Literal["Y", "R"]
    # New RAW intentionally does not retain the operator UID.
    createdBy: str = ""
    createdAt: datetime


class ExportJobDoc(SchemaModel):
    stage: Literal["kpi", "rating", "assemble", "sent"]
    kpiVersion: int
    attempts: int
    error: str | None
    updatedAt: datetime


class PathSnapshotDoc(SchemaModel):
    gtId: str
    recordIds: list[str]
    ptype: Literal["UPP", "UTP", "DTP", "STP"]
    dsp: bool
    ttp: bool
    resCode: ResCode


class PlayerStatsDoc(SchemaModel):
    playerId: str
    TAP: int | float
    DAP: int | float
    UTP: int | float
    DTP: int | float
    TTP: int | float
    SHOT: int | float
    AST: int | float
    GOAL: int | float
    DTB: int | float
    DTM: int | float
    DTA: int | float
    DTS: int | float
    GTB: int | float
    GTM: int | float
    ASR: int | float
    SSR: int | float
    scoreRel: float | None
    scoreAbs: float | None
    score: float | None
    jmx: float | None
    apx: float | None
    apxGrade: str | None
    tpx: float | None
    tpxGrade: str | None
    fpx: float | None
    fpxGrade: str | None
    ratingBasedOn: int | None


class AuditFields(SchemaModel):
    createdAt: datetime
    createdBy: str
    updatedAt: datetime
    updatedBy: str


class PlayerDoc(AuditFields):
    name: str
    nameEn: str | None = None
    nameFull: str | None = None
    birth: str | None = None
    height: int | float | None = None
    foot: Literal["L", "R", "B"] | None = None
    nation: str | None = None
    active: bool
    currentTeamId: str | None = None
    currentNo: str | None = None
    currentPos: str | None = None
    legacyPlayerId: int | None = None


class ContractDoc(AuditFields):
    teamId: str
    leagueId: str | None = None
    seasonId: str | None = None
    from_: str = Field(alias="from")
    to: str | None
    no: str | None = None
    pos: str | None = None
    fromTeamId: str | None = None
    transferType: Literal["TRANSFER", "LOAN", "LOAN_RETURN", "FREE", "YOUTH", "RETIRE"] | None = None
    fee: int | float | None = None
    currency: str | None = None


class SquadEntry(SchemaModel):
    name: str
    no: str
    pos: str
    contractId: str
    since: str


class CoachDoc(AuditFields):
    name: str
    nameKr: str | None = None
    nameEn: str | None = None
    birth: str | None = None
    nation: str | None = None
    active: bool
    currentTeamId: str | None = None
    legacyCoachId: int | None = None


class CoachContractDoc(AuditFields):
    teamId: str
    leagueId: str | None = None
    seasonId: str | None = None
    from_: str = Field(alias="from")
    to: str | None
    fromTeamId: str | None = None


class TeamDoc(AuditFields):
    name: str
    nameKr: str | None = None
    nameFull: str | None = None
    nameShort: str | None = None
    textColor: str | None = None
    stadiumId: str | None = None
    currentCoachId: str | None = None
    foundedAt: str | None = None
    dissolvedAt: str | None = None
    currentLeagueId: str | None = None
    crestUrl: str | None = None


class TeamSeasonEntry(SchemaModel):
    leagueId: str
    division: str | None = None
    finalRank: int | None = None


class LeagueDoc(AuditFields):
    name: str
    nameEn: str | None = None
    country: str | None = None


class SeasonDoc(AuditFields):
    leagueId: str
    name: str
    from_: str = Field(alias="from")
    to: str
    alias: str | None = None


class StadiumDoc(AuditFields):
    name: str
    nameKr: str | None = None
    seats: int | None = None
    country: str | None = None
    city: str | None = None
    homeTeamId: str | None = None
    surface: str | None = None


class RecorderProfileDoc(SchemaModel):
    name: str
    teamId: str | None = None
    role: Literal["recorder", "admin"]
    level: RecorderLevel
    handedness: Literal["L", "R"] | None = None


class PlayerKpi(SchemaModel):
    playerId: str
    TAP: int | float
    DAP: int | float
    UTP: int | float
    DTP: int | float
    TTP: int | float
    SHOT: int | float
    AST: int | float
    GOAL: int | float
    DTB: int | float
    DTM: int | float
    DTA: int | float
    DTS: int | float
    GTB: int | float
    GTM: int | float
    ASR: int | float
    SSR: int | float


class TeamKpi5Min(SchemaModel):
    DAP: int | float
    DTP: int | float
    SHOT: int | float
    SSR: int | float
    GOAL: int | float
