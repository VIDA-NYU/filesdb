"""Get projects, versions, and downloads from BigQuery data.

The most reliable source of information for PyPI is now on Google BigQuery. It
is the only way to get recent updates, bulk data, and some fields like download
counts.

This script allows you to import a CSV exported from BigQuery into the
database.

Use this SQL query on BigQuery:

    SELECT
        name, version,
        upload_time, filename, size,
        path,
        python_version, packagetype,
        md5_digest, sha256_digest
    FROM `the-psf.pypi.distribution_metadata`
    WHERE upload_time > :last_upload_time
    ORDER BY upload_time ASC
"""

import csv
from datetime import datetime
import logging
import os
import re
import sys

from . import database


logger = logging.getLogger('filesdb.read_bigquery')


class BatchInserter(object):
    BATCH_SIZE = 500

    def __init__(self, db, query, dependencies=()):
        self.db = db
        self.query = query
        self.values = []
        self.dependencies = dependencies

    def insert(self, **kwargs):
        self.values.append(kwargs)
        if len(self.values) > self.BATCH_SIZE:
            values = self.values[:self.BATCH_SIZE]
            self.values = self.values[self.BATCH_SIZE:]

            for dep in self.dependencies:
                dep.flush()
            query = self.query.values(values)
            self.db.execute(query)

    def flush(self):
        if self.values:
            for dep in self.dependencies:
                dep.flush()

            query = self.query.values(self.values)
            self.db.execute(query)
            self.values = []


def main():
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) != 2 or not os.path.isfile(sys.argv[1]):
        print("Usage: read_bigquery.py <exported-table.csv>", file=sys.stderr)
        sys.exit(2)
    filename = sys.argv[1]

    with open(filename, 'r') as fp:
        total_rows = sum(1 for _ in fp) - 1

    with open(filename, 'r') as fp:
        reader = csv.DictReader(fp)

        header = reader.fieldnames
        assert header == [
            'name', 'version',
            'upload_time', 'filename', 'size',
            'path',
            'python_version', 'packagetype',
            'md5_digest', 'sha256_digest',
        ]

        with database.connect() as db:
            projects = BatchInserter(
                db,
                database.insert_or_ignore(database.projects),
            )
            versions = BatchInserter(
                db,
                database.insert_or_ignore(database.project_versions),
                [projects],
            )
            downloads = BatchInserter(
                db,
                database.insert_or_ignore(database.downloads),
                [projects],
            )
            for i, row in enumerate(reader):
                if i % 10000 == 0:
                    print("%d / %d" % (i, total_rows))

                if row['path']:
                    url = 'https://files.pythonhosted.org/packages/' + row['path']
                else:
                    url = None

                timestamp = row['upload_time']
                timestamp = re.sub(
                    r'^(20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ([0-9][0-9]:[0-9][0-9]:[0-9][0-9])(?:\.[0-9]*)? UTC$',
                    r'\1T\2',
                    timestamp,
                )
                timestamp = datetime.fromisoformat(timestamp)

                projects.insert(
                    name=row['name'],
                )
                versions.insert(
                    project_name=row['name'],
                    version=row['version'],
                )
                downloads.insert(
                    project_name=row['name'],
                    project_version=row['version'],
                    name=row['filename'],
                    size_bytes=row['size'],
                    upload_time=timestamp,
                    url=url,
                    type=row['packagetype'],
                    python_version=row['python_version'],
                    hash_md5=row['md5_digest'],
                    hash_sha256=row['sha256_digest'],
                )


if __name__ == '__main__':
    main()