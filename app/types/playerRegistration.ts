export interface PlayerRegistration {
  leagueId: string
  seasonId: string
  teamId: string
  matchId: string
  kind: 'NEW' | 'YOUTH' | 'TRANSFER' | 'LOAN'
  name: string
  nameEn: string
  nameFull: string
  birth: string
  nation: string
  no: string
  pos: 'GK' | 'DF' | 'MF' | 'FW'
  foot: '' | 'L' | 'R' | 'B'
  height: number | null
  fromDate: string
  sourceUrl: string
  note: string
}

export function emptyPlayerRegistration(): PlayerRegistration {
  return { leagueId: '', seasonId: '', teamId: '', matchId: '', kind: 'YOUTH', name: '', nameEn: '', nameFull: '',
    birth: '', nation: '', no: '', pos: 'MF', foot: '', height: null, fromDate: '', sourceUrl: '', note: '' }
}

export interface PlayerRegistrationRequest {
  id: string
  payload: PlayerRegistration
  status: 'pending' | 'approved'
  version: number
  requestedBy: string
  requestedName: string
  teamName: string
  leagueName: string
  playerId?: string
  createdAt?: string
}
