// Frontend display and management form shapes only.
// The backend storage and pipeline contract is defined in backend/system/system_schema.py.

import type { Timestamp } from 'firebase/firestore'

export type Half = 'H1' | 'H2' | 'H3' | 'H4'

export type HalfStatus =
  | 'ready'
  | 'H1' | 'H1_done'
  | 'H2' | 'H2_done'
  | 'H3' | 'H3_done'
  | 'H4' | 'H4_done'
  | 'final'

export type RecorderLevel = 'basic' | 'advanced'

interface AuditFields {
  createdAt: Timestamp
  createdBy: string
  updatedAt: Timestamp
  updatedBy: string
}

export interface MatchDoc {
  date: string
  kickoffTime?: string
  leagueId: string
  seasonId: string
  matchType: 'league' | 'tournament'
  round?: number
  stage?: 'R32' | 'R16' | 'QF' | 'SF' | 'F'
  group?: string
  leg?: 1 | 2
  stadiumId: string
  homeTeamId: string
  awayTeamId: string
  score: { home: number; away: number }
  createdAt: Timestamp
  updatedAt: Timestamp
}

export interface PlayerDoc extends AuditFields {
  name: string
  nameEn?: string
  nameFull?: string
  birth?: string
  height?: number
  foot?: 'L' | 'R' | 'B'
  nation?: string
  searchTerms?: string[]
  birthKey?: string
  active: boolean
  currentTeamId?: string
  currentNo?: string
  currentPos?: string
  legacyPlayerId?: number
}

export interface ContractDoc extends AuditFields {
  teamId: string
  leagueId?: string
  competitionType?: 'league' | 'cup' | 'national' | 'test'
  seasonId?: string
  from: string
  to: string | null
  no?: string
  pos?: string
  fromTeamId?: string
  transferType?: 'TRANSFER' | 'LOAN' | 'LOAN_RETURN' | 'FREE' | 'YOUTH' | 'RETIRE'
  fee?: number
  currency?: string
}

export interface CoachContractDoc extends AuditFields {
  coachId: string
  coachName: string
  coachNameEn: string
  teamId: string
  from: string
  to: string | null
  status: 'MANAGER' | 'CARETAKER'
}

export interface TeamDoc extends AuditFields {
  name: string
  active?: boolean
  nameKr?: string
  nameFull?: string
  nameShort?: string
  textColor?: string
  stadiumId?: string
  currentCoachId?: string
  foundedAt?: string
  dissolvedAt?: string
  currentLeagueId?: string
  currentCupIds?: string[]
  currentDivision?: 'D1' | 'D2'
  crestUrl?: string
}

export interface TeamSeasonEntry {
  leagueId?: string
  competitionIds?: string[]
  leagueIds?: string[]
  cupIds?: string[]
  division?: string
  finalRank?: number
}

export interface LeagueDoc extends AuditFields {
  name: string
  nameEn?: string
  country?: string
  competitionType?: 'league' | 'cup' | 'national' | 'test'
}

export interface StadiumDoc extends AuditFields {
  name: string
  nameKr?: string
  seats?: number
  country?: string
  city?: string
  homeTeamId?: string
  surface?: string
}
