import gc
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from core.repositories.assembly_configuration_repository import (
    AssemblyConfigurationRepository,
)
from core.repositories.bom_revision_repository import BomRevisionRepository
from core.services.assembly_configuration_service import AssemblyConfigurationService


class _ProjectService:
    def __init__(self, working_directory):
        self.working_directory = working_directory

    def get_project_by_id(self, project_id):
        return {
            "id": int(project_id),
            "working_directory": self.working_directory,
            "version_label": "D",
        }


class _NoAudit:
    @staticmethod
    def supported():
        return False


class AssemblyConfigurationServiceTests(unittest.TestCase):
    def test_draft_freeze_version_and_build_flow(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = os.path.join(root, "nexus.db")
            working = os.path.join(root, "project")
            os.makedirs(working)
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);
                    CREATE TABLE projects(
                        id INTEGER PRIMARY KEY, name TEXT, working_directory TEXT,
                        version_label TEXT
                    );
                    CREATE TABLE bom(
                        id INTEGER PRIMARY KEY, type TEXT, name TEXT, aes_number TEXT,
                        part_number TEXT, drawing_number TEXT, filename TEXT, drawing TEXT,
                        base_file_name TEXT, base_drw_name TEXT, material TEXT, weight TEXT,
                        notes TEXT, pdf_path TEXT, step_path TEXT, revision TEXT,
                        lifecycle_state TEXT, status TEXT, modified TEXT, project_id INTEGER
                    );
                    CREATE TABLE bom_children(
                        id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER,
                        child_id INTEGER, quantity INTEGER, sort_order INTEGER
                    );
                    INSERT INTO users(id,username) VALUES(1,'designer');
                    INSERT INTO projects(id,name,working_directory,version_label)
                    VALUES(10,'Demo','', 'D');
                    INSERT INTO bom(
                        id,type,name,aes_number,filename,drawing,revision,
                        lifecycle_state,status,project_id
                    ) VALUES
                        (1,'asm','Prototype Assembly','A01','prototype.asm.7',
                         'prototype.drw.3','D','WIP','Design',10),
                        (2,'prt','Printed Bracket','P01','bracket.prt.12','',
                         'B','WIP','Design',10);
                    INSERT INTO bom_children(parent_id,child_id,quantity,sort_order)
                    VALUES(1,2,2,10);
                    """
                )
                conn.commit()
            finally:
                conn.close()
            original_files = {
                "prototype.asm.7": b"frozen assembly",
                "prototype.drw.3": b"frozen drawing",
                "bracket.prt.12": b"frozen part",
            }
            for filename, content in original_files.items():
                with open(os.path.join(working, filename), "wb") as handle:
                    handle.write(content)

            revision_repo = BomRevisionRepository(db_path)
            configuration_repo = AssemblyConfigurationRepository(db_path)
            service = object.__new__(AssemblyConfigurationService)
            service.session = SimpleNamespace(user_id=1, project_id=10)
            service.repo = configuration_repo
            service.revision_repo = revision_repo
            service.project_service = _ProjectService(working)
            service.audit_service = _NoAudit()

            root_iteration = revision_repo.get_current_context(1)["current_iteration_id"]
            configuration = service.create_configuration(
                project_id=10,
                root_bom_id=1,
                root_iteration_id=root_iteration,
                name="3D Print Trial",
                purpose="3D Printing",
            )
            self.assertEqual(configuration["member_count"], 2)
            self.assertEqual(configuration["file_count"], 0)
            self.assertEqual(configuration["state"], "Draft")
            self.assertEqual(configuration["storage_rel_path"], "")
            self.assertFalse(
                os.path.exists(os.path.join(working, ".nexus", "configurations"))
            )

            configuration = service.freeze_configuration(int(configuration["id"]))
            self.assertEqual(configuration["state"], "Frozen")
            self.assertEqual(configuration["file_count"], 3)
            next_version = service.create_new_version(int(configuration["id"]))
            self.assertEqual(next_version["state"], "Draft")
            self.assertEqual(next_version["version_number"], 2)
            with self.assertRaises(ValueError):
                service.build_configuration(
                    int(next_version["id"]), os.path.join(root, "draft-build")
                )

            result = service.build_configuration(
                int(configuration["id"]), os.path.join(root, "rebuilt")
            )

            for filename, expected in original_files.items():
                with open(os.path.join(result["target_directory"], filename), "rb") as handle:
                    self.assertEqual(handle.read(), expected)
            self.assertTrue(os.path.isfile(result["root_file_path"]))
            self.assertTrue(
                os.path.isfile(
                    os.path.join(result["target_directory"], "nexus_configuration_manifest.json")
                )
            )
            del service, configuration_repo, revision_repo
            gc.collect()


if __name__ == "__main__":
    unittest.main()
