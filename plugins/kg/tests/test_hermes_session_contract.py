"""Read the installed host's schema as data and populate only a synthetic DB."""
from __future__ import annotations

import ast
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kg.hermes_runtime import discover_core
from kg.provenance import groundable_user_messages
from kg.session_source import SessionSource, SourceChangedError


CORE=discover_core()


@unittest.skipUnless(CORE and (CORE/'hermes_state_common.py').is_file(), 'Installed Hermes schema is required')
class HermesSessionContractTests(unittest.TestCase):
    def test_current_host_schema_preserves_provenance_and_detects_metadata_edits(self):
        syntax=ast.parse((CORE/'hermes_state_common.py').read_text(encoding='utf-8'))
        schema=next(ast.literal_eval(node.value) for node in syntax.body
                    if isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id=='SCHEMA_SQL' for name in node.targets))
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            with closing(sqlite3.connect(home/'state.db')) as db:
                db.executescript(schema)
                db.execute('INSERT INTO sessions(id,source,started_at) VALUES(?,?,?)',('synthetic','cli',100.0))
                rows=[('Visible human fact',1,0,None),('Inactive user text',0,0,None),
                      ('Synthetic hidden scaffold',1,0,'hidden'),('Compression summary',1,1,None),
                      ('User steering text',1,0,'steer')]
                db.executemany('INSERT INTO messages(session_id,role,content,timestamp,active,_compressed_summary,display_kind) VALUES(\'synthetic\',\'user\',?,100.0,?,?,?)',rows)
                db.commit()
                source=SessionSource(home)
                self.assertEqual(source.metadata('synthetic')['source'],'cli')
                snapshot=source.read_snapshot('synthetic')
                self.assertEqual([row['content'] for row in groundable_user_messages(snapshot)],
                                 ['Visible human fact','User steering text'])
                source.validate_snapshot(snapshot)
                db.execute('UPDATE messages SET display_kind=\'hidden\' WHERE content=\'Visible human fact\'')
                db.commit()
                with self.assertRaises(SourceChangedError):
                    source.validate_snapshot(snapshot)


if __name__=='__main__':
    unittest.main()
