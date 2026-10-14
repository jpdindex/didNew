export interface LiveCommand {
  commandId: string
  key: string
  uid: string
  gmId: string
  side: 'H' | 'A'
  kind: 'draft' | 'setup' | 'finalize' | 'restore'
  request: Record<string, any>
  order?: number
  blocked?: string
  attempted?: boolean
}

// Separate from UI checkpoints: a snapshot is never an offline command.
export class LiveCommandStore {
  private async open(): Promise<IDBDatabase> {
    return await new Promise((resolve, reject) => {
      const request = indexedDB.open('jpd-did-live-commands', 1)
      request.onupgradeneeded = () => {
        request.result.createObjectStore('commands', { keyPath: 'order', autoIncrement: true })
      }
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
  }

  private async transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await this.open()
    try {
      return await new Promise((resolve, reject) => {
        const transaction = db.transaction('commands', mode)
        const request = action(transaction.objectStore('commands'))
        // Request success alone does not mean the transaction was committed.
        transaction.oncomplete = () => resolve(request.result)
        transaction.onerror = () => reject(transaction.error)
        transaction.onabort = () => reject(transaction.error || new Error('Outbox transaction aborted'))
      })
    } finally { db.close() }
  }

  async add(command: LiveCommand) {
    return Number(await this.transaction('readwrite', store => store.add(JSON.parse(JSON.stringify(command)))))
  }

  async list(key: string): Promise<LiveCommand[]> {
    const commands = await this.transaction<LiveCommand[]>('readonly', store => store.getAll())
    return commands.filter(command => command.key === key).sort((a, b) => a.order! - b.order!)
  }

  async update(command: LiveCommand) {
    await this.transaction('readwrite', store => store.put(JSON.parse(JSON.stringify(command))))
  }

  async remove(order: number) {
    await this.transaction('readwrite', store => store.delete(order))
  }
}
