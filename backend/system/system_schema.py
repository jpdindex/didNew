from __future__ import annotations

from dataclasses import dataclass
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
RecorderRank = Literal["main", "sub", "manager"]
DataSource = Literal["did", "vision"]
ActCode = Literal["C", "P", "K", "F", "S", "H", "R", ""]
ResCode = Literal["O", "X", "B", "GB", "GX", "GOAL", "L", "H", "R", "LX", "HX", "RX", ""]
# Legacy SQL path rows use "G" while individual raw records are normalized to
# GOAL by RecordDoc below. Preserve the imported path value without rewriting
# historical match documents.
PathResCode = Literal["O", "X", "B", "GB", "GX", "G", "GOAL", "L", "H", "R", "LX", "HX", "RX", ""]


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
    # Display name at finalize time. Legacy imports have no name.
    name: str | None = None


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
    # Final analyst roster copied from the Draft at promotion, because the
    # promoted Draft is deleted. primary=main, assistant=sub, manager=manager.
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
    # SQL migration-only evidence. New DID input never populates these fields.
    legacyPathId: str | None = None
    legacyPathType: Literal["UPP", "UTP", "DTP", "STP"] | None = None
    legacyPathTtp: bool | None = None
    legacyKpiFlags: dict[str, bool] | None = None
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
    resCode: PathResCode


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


@dataclass(frozen=True)
class StorageDocumentContract:
    """The persisted DID contract for one match-tree document shape.

    ``common_required`` is the minimum shared shape produced by both the SQL
    importer and the new-input promotion path.  ``legacy_optional`` preserves
    SQL provenance without making it a requirement for new DID RAW.
    """

    model: type[SchemaModel]
    common_required: frozenset[str]
    legacy_optional: frozenset[str] = frozenset()
    legacy_only: bool = False


DID_STORAGE_CONTRACTS: dict[str, StorageDocumentContract] = {
    "match": StorageDocumentContract(
        model=MatchDoc,
        common_required=frozenset({
            "date", "leagueId", "seasonId", "matchType", "stadiumId",
            "homeTeamId", "awayTeamId", "score", "createdAt", "updatedAt",
        }),
    ),
    "recording": StorageDocumentContract(
        model=RecordingDoc,
        common_required=frozenset({
            "side", "teamId", "opponentTeamId", "status", "inputMode",
            "fieldSide", "formationKey", "lineup", "halves", "h1Locked",
            "h2Locked", "maxSeq", "createdAt", "updatedAt",
        }),
        legacy_optional=frozenset({"legacyGiId", "legacyRecorderId"}),
    ),
    "record": StorageDocumentContract(
        model=RecordDoc,
        common_required=frozenset({
            "half", "halfSeconds", "seq", "act", "res", "area", "source", "createdAt",
        }),
        legacy_optional=frozenset({
            "legacyPathId", "legacyPathType", "legacyPathTtp", "legacyKpiFlags",
        }),
    ),
    "card": StorageDocumentContract(
        model=CardDoc,
        common_required=frozenset({"playerId", "half", "halfSeconds", "card", "createdAt"}),
    ),
    "playerStats": StorageDocumentContract(
        model=PlayerStatsDoc,
        common_required=frozenset({
            "playerId", "TAP", "DAP", "UTP", "DTP", "TTP", "SHOT", "AST", "GOAL",
            "DTB", "DTM", "DTA", "DTS", "GTB", "GTM", "ASR", "SSR",
        }),
    ),
    # SQL path rows are KPI evidence only. New DID input has no equivalent
    # operator concept and must not be forced to create them.
    "path": StorageDocumentContract(
        model=PathSnapshotDoc,
        common_required=frozenset(),
        legacy_only=True,
    ),
}


def storage_contract_for_path(document_path: str) -> StorageDocumentContract | None:
    """Return the canonical contract for a persisted match-tree path."""
    parts = document_path.split("/")
    if len(parts) == 2 and parts[0] == "matches":
        return DID_STORAGE_CONTRACTS["match"]
    if len(parts) == 4 and parts[0] == "matches" and parts[2] == "recordings":
        return DID_STORAGE_CONTRACTS["recording"]
    if len(parts) != 6 or parts[0] != "matches" or parts[2] != "recordings":
        return None
    return {
        "records": DID_STORAGE_CONTRACTS["record"],
        "cards": DID_STORAGE_CONTRACTS["card"],
        "playerStats": DID_STORAGE_CONTRACTS["playerStats"],
        "paths": DID_STORAGE_CONTRACTS["path"],
    }.get(parts[4])


def storage_field_scope(document_path: str, field_path: tuple[str, ...] = ()) -> str:
    """Classify a field for diagnostics without treating legacy evidence as RAW."""
    contract = storage_contract_for_path(document_path)
    if contract is None:
        return "outside-contract"
    if contract.legacy_only:
        return "legacy-only"
    field = field_path[0] if field_path else None
    if field in contract.legacy_optional:
        return "legacy-optional"
    if field in contract.common_required:
        return "common-required"
    return "common-optional"


def storage_field_expectation(document_path: str, field_path: tuple[str, ...] = ()) -> str:
    """Human-readable expected field contract for API/audit diagnostics."""
    contract = storage_contract_for_path(document_path)
    if contract is None:
        return "no DID storage contract for this document path"
    if not field_path:
        return f"{storage_field_scope(document_path)} document validated by {contract.model.__name__}"
    field = contract.model.model_fields.get(field_path[0])
    if field is None:
        return "field is not permitted by the DID storage contract"
    requirement = "required" if field.is_required() else "optional"
    return f"{storage_field_scope(document_path, field_path)} {requirement} field: {field.annotation}"


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
    competitionType: Literal["league", "cup", "national", "test"] | None = None
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
    active: bool = True
    nameKr: str | None = None
    nameFull: str | None = None
    nameShort: str | None = None
    textColor: str | None = None
    stadiumId: str | None = None
    currentCoachId: str | None = None
    foundedAt: str | None = None
    dissolvedAt: str | None = None
    currentLeagueId: str | None = None
    currentCupIds: list[str] = Field(default_factory=list)
    crestUrl: str | None = None


class TeamSeasonEntry(SchemaModel):
    # ``competitionIds`` is authoritative because a club can be in a domestic
    # league and UCL/UEL during the same season.  ``leagueId`` remains a
    # compatibility cache for existing administrative screens.
    leagueId: str | None = None
    competitionIds: list[str] = Field(default_factory=list)
    leagueIds: list[str] = Field(default_factory=list)
    cupIds: list[str] = Field(default_factory=list)
    division: str | None = None
    finalRank: int | None = None


class LeagueDoc(AuditFields):
    name: str
    nameEn: str | None = None
    country: str | None = None
    competitionType: Literal["league", "cup", "national", "test"] | None = None


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
