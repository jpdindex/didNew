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
    recorders: dict[str, RecorderEntry]
    recorderIds: list[str]
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
    # Legacy derived fields may exist on imported documents. New raw recordings
    # do not write them; the derived result belongs to matchKpis instead.
    kpi: RecordingKpi | None = None
    kpiComputedAt: datetime | None = None
    kpiVersion: int = 0
    teamRating: TeamRatingSnapshot | None = None
    ratingBasedOn: int | None = None
    legacyGiId: int | None = None
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
    createdBy: str
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
    createdBy: str
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
    TAP: int
    DAP: int
    UTP: int
    DTP: int
    TTP: int
    SHOT: int
    AST: int
    GOAL: int
    DTB: int
    DTM: int
    DTA: int
    DTS: int
    GTB: int
    GTM: int
    ASR: int
    SSR: int


class TeamKpi5Min(SchemaModel):
    DAP: int
    DTP: int
    SHOT: int
    SSR: int
    GOAL: int


class MatchKpiRating(SchemaModel):
    teamRating: TeamRatingSnapshot | None = None
    playerRatings: dict[str, dict[str, float | str | None]] = Field(default_factory=dict)
    basedOnKpiVersion: int | None = None
    calculatedAt: datetime | None = None


class MatchKpiDoc(SchemaModel):
    """Derived output only. Source records always remain under matches/{gm_id}."""

    schemaVersion: int = 1
    gmId: str
    side: Side
    leagueId: str
    seasonId: str
    matchType: Literal["league", "tournament"]
    round: int | None = None
    stage: str | None = None
    group: str | None = None
    leg: int | None = None
    teamId: str
    opponentTeamId: str
    sourceRecordCount: int
    sourceFingerprint: str
    kpiVersion: int
    teamKpi: RecordingKpi
    teamKpi5min: list[TeamKpi5Min] = Field(min_length=20, max_length=20)
    playerKpis: dict[str, PlayerKpi]
    status: Literal["kpi_ready", "rating_pending", "rated", "footballx_pending", "sent", "failed"]
    rating: MatchKpiRating = Field(default_factory=MatchKpiRating)
    calculatedAt: datetime
    updatedAt: datetime
