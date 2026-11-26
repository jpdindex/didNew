const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { ref, computed } = require('vue')
const { transformSync } = require('../../node_modules/.pnpm/node_modules/esbuild')

test('input scoreboard follows the selected team side and half without changing score ownership', () => {
  const source = fs.readFileSync('app/pages/DidInput.vue', 'utf8')
  const side = source.match(/^const gi_part = .*$/m)[0]
  const scoreboard = source.slice(source.indexOf('const team = computed'), source.indexOf('// 입력 중인 팀의 스코어'))
  for (const selectedTeam of ['home', 'away']) {
    for (const fieldSide of ['left', 'right']) {
      const game = ref({ side: fieldSide })
      const half = ref('전반')
      const homeScore = ref(1)
      const awayScore = ref(2)
      const context = vm.createContext({
        computed, game, half, homeScore, awayScore,
        route: { query: { team: selectedTeam, side: fieldSide === 'left' ? 'right' : 'left' } },
        home: ref('입스위치'), away: ref('본머스'),
      })
      vm.runInContext(transformSync(`${side}\n${scoreboard}\nglobalThis.state = { leftTeam, rightTeam, leftTeamName, rightTeamName, leftScore, rightScore };`, { loader: 'ts' }).code, context)
      const state = context.state
      for (const period of ['전반', '후반', '전반']) {
        half.value = period
        const ownOnLeft = (fieldSide === 'left') === (period === '전반')
        const left = ownOnLeft ? selectedTeam : selectedTeam === 'home' ? 'away' : 'home'
        assert.equal(state.leftTeam.value, left)
        assert.equal(state.rightTeam.value, left === 'home' ? 'away' : 'home')
        assert.equal(state.leftTeamName.value, left === 'home' ? '입스위치' : '본머스')
        assert.equal(state.rightTeamName.value, left === 'home' ? '본머스' : '입스위치')
        assert.equal(state.leftScore.value, left === 'home' ? 1 : 2)
        assert.equal(state.rightScore.value, left === 'home' ? 2 : 1)
      }
      awayScore.value = 3
      assert.equal((state.leftTeam.value === 'away' ? state.leftScore : state.rightScore).value, 3)
      assert.equal(homeScore.value, 1)
      assert.equal(game.value.side, fieldSide)
    }
  }
})
