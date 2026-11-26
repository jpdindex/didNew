"""List active public jobs or cancel one queued job; standard library only.

Run inside the existing backend instance, with its actual delivery.sqlite3 path.
Listing is read-only. Cancellation never changes a running job.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--cancel', metavar='JOB_ID')
    args = parser.parse_args()
    # mode=rw prevents accidentally creating a new database at the wrong path.
    uri = args.db.resolve().as_uri() + ('?mode=rw' if args.cancel else '?mode=ro')
    with sqlite3.connect(uri, uri=True, timeout=30) as db:
        db.row_factory = sqlite3.Row
        if args.cancel:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE id=? AND public=1 "
                             "AND kind IN ('delivery','preview','goalx_rebuild')", (args.cancel,)).fetchone()
            if row is None:
                raise SystemExit('Job not found; nothing changed.')
            if row['state'] != 'queued':
                raise SystemExit(f"Job is {row['state']}, not queued; nothing changed.")
            db.execute("UPDATE jobs SET state='cancelled',updated=?,error=NULL WHERE id=? AND state='queued'",
                       (time.time(), args.cancel))
            db.commit()
            print(json.dumps({'jobId': args.cancel, 'status': 'cancelled',
                              'scope': json.loads(row['scope'])}, ensure_ascii=False))
        else:
            rows = db.execute("SELECT id,kind,state,scope FROM jobs WHERE public=1 "
                              "AND state IN ('queued','running') ORDER BY created").fetchall()
            for row in rows:
                print(json.dumps({**dict(row), 'scope': json.loads(row['scope'])}, ensure_ascii=False))
            if not rows:
                print('No queued or running public jobs.')


if __name__ == '__main__':
    main()
