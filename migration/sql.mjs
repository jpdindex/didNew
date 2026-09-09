// Parse explicit-column MySQL INSERTs without splitting quoted semicolons or IDs.
export function parseSql(sql) {
  const tables = new Map()
  const re = /INSERT\s+INTO\s+`?(\w+)`?\s*\(([^)]+)\)\s*VALUES\s*/gi
  let match
  while ((match = re.exec(sql))) {
    const columns = match[2].split(',').map(x => x.trim().replaceAll('`', ''))
    const rows = tables.get(match[1]) ?? []
    let values = [], token = '', quoted = false, stringValue = false, depth = 0, ended = false
    const value = () => {
      const raw = token.trim()
      if (stringValue) return token
      if (/^NULL$/i.test(raw)) return null
      if (!/^-?\d+(\.\d+)?(?:e[+-]?\d+)?$/i.test(raw)) throw Error(`Unsupported SQL value: ${raw.slice(0, 80)}`)
      const number = Number(raw)
      if (!Number.isFinite(number) || (Number.isInteger(number) && !Number.isSafeInteger(number))) throw Error('Unsafe SQL number')
      return number
    }
    for (let i = re.lastIndex; i < sql.length; i++) {
      const c = sql[i]
      if (quoted) {
        if (c === '\\') { const next = sql[++i]; token += ({n:'\n',r:'\r',t:'\t','0':'\0',b:'\b',Z:'\x1a'})[next] ?? next }
        else if (c === "'") { if (sql[i+1] === "'") { token += "'"; i++ } else quoted = false }
        else token += c
      } else if (c === "'") { quoted = true; stringValue = true; token = '' }
      else if (c === '(') { if (depth++) throw Error('Nested SQL expression unsupported'); values = []; token = ''; stringValue = false }
      else if ((c === ',' || c === ')') && depth) {
        values.push(value()); token = ''; stringValue = false
        if (c === ')') { depth--; if (values.length !== columns.length) throw Error(`Column mismatch in ${match[1]}`); rows.push(Object.fromEntries(columns.map((key,j)=>[key,values[j]]))) }
      } else if (c === ';') { re.lastIndex = i+1; ended = true; break }
      else if (depth && !(stringValue && /\s/.test(c))) token += c
    }
    if (!ended || quoted || depth) throw Error(`Unterminated INSERT in ${match[1]}`)
    tables.set(match[1], rows)
  }
  const inserts = (sql.match(/\bINSERT\s+INTO\b/gi) ?? []).length
  if (inserts && !tables.size) throw Error('No supported explicit-column INSERT found')
  return tables
}
