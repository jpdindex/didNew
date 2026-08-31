// 임시 하드코딩 스쿼드. Firestore 연동 전까지만 사용한다.
// TeamSelection.vue 에도 같은 데이터가 있으며, 참조 데이터 관리 화면을 만들 때 함께 정리한다.

export interface SquadPlayer {
  /** 등번호. 현재는 이것을 선수 식별자(playerId)로 쓴다. */
  no: string
  name: string
  pos: 'GK' | 'FW' | 'MF' | 'DF'
}

const raw = (rows: [string, string, string][]): SquadPlayer[] =>
  rows.map(([no, name, pos]) => ({ no, name, pos: pos as SquadPlayer['pos'] }))

export const HOME_SQUAD: SquadPlayer[] = raw([
  ['1', 'D. Raya', 'GK'], ['13', 'A. Ramsdale', 'GK'], ['30', 'M. Turner', 'GK'],
  ['7', 'B. Saka', 'FW'], ['9', 'G. Jesus', 'FW'], ['12', 'J. Timber', 'FW'], ['14', 'E. Nketiah', 'FW'],
  ['18', 'T. Tomiyasu', 'FW'], ['19', 'L. Trossard', 'FW'], ['21', 'F. Vieira', 'FW'], ['28', 'Marquinhos', 'FW'],
  ['6', 'Gabriel', 'MF'], ['8', 'Odegaard', 'MF'], ['10', 'Smith Rowe', 'MF'], ['11', 'Martinelli', 'MF'],
  ['15', 'Kiwior', 'MF'], ['17', 'Soares', 'MF'], ['23', 'Lokonga', 'MF'], ['29', 'Havertz', 'MF'],
  ['2', 'Saliba', 'DF'], ['3', 'Tierney', 'DF'], ['4', 'B. White', 'DF'], ['5', 'Partey', 'DF'],
  ['16', 'Holding', 'DF'], ['20', 'Jorginho', 'DF'], ['22', 'P. Mari', 'DF'], ['24', 'Nelson', 'DF'],
  ['26', 'Balogun', 'DF'], ['27', 'M. Smith', 'DF'],
])

export const AWAY_SQUAD: SquadPlayer[] = raw([
  ['1', 'T. Courtois', 'GK'], ['13', 'A. Lunin', 'GK'], ['26', 'K. Fernandez', 'GK'],
  ['9', 'K. Mbappe', 'FW'], ['7', 'Vinicius Jr', 'FW'], ['11', 'R. Diaz', 'FW'], ['14', 'Endrick', 'FW'],
  ['20', 'Rodrygo', 'FW'], ['24', 'A. Gonzalez', 'FW'], ['21', 'B. Mayoral', 'FW'], ['17', 'L. Vazquez', 'FW'],
  ['5', 'Jude Bellingham', 'MF'], ['15', 'F. Valverde', 'MF'], ['12', 'Eduardo Camavinga', 'MF'],
  ['8', 'Toni Kroos', 'MF'], ['19', 'D. Ceballos', 'MF'], ['6', 'Nacho', 'MF'],
  ['22', 'Aurelien Tchouameni', 'MF'], ['16', 'A. Modric', 'MF'],
  ['4', 'David Alaba', 'DF'], ['2', 'Dani Carvajal', 'DF'], ['3', 'Eder Militao', 'DF'],
  ['23', 'Fran Garcia', 'DF'], ['18', 'Alvaro Odriozola', 'DF'], ['25', 'Antonio Rudiger', 'DF'],
  ['27', 'Nacho Fernandez', 'DF'], ['28', 'Jesus Vallejo', 'DF'], ['32', 'Rafa Marin', 'DF'],
  ['35', 'Chema Andres', 'DF'],
])
