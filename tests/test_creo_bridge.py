import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from core.integrations.creo_bridge import (
    BridgeApiError,
    CreoBridgeController,
    NexusCreoBridge,
)


class _Controller:
    def dispatch(self, method, path, query, body):
        return {
            "method": method,
            "path": path,
            "query": query,
            "body": body,
        }


class _CadRepo:
    def __init__(self):
        self.documents = {
            1: {
                "id": 1,
                "project_id": 9,
                "file_name": "machine.asm",
                "checked_out_by": 7,
                "checkout_workspace_id": "workspace-one",
                "checkout_workspace_machine_id": "",
            },
            2: {
                "id": 2,
                "project_id": 9,
                "file_name": "frame.asm",
                "checked_out_by": None,
            },
            3: {
                "id": 3,
                "project_id": 9,
                "file_name": "bracket.prt",
                "checked_out_by": 8,
                "checkout_workspace_id": "other-workspace",
            },
            4: {
                "id": 4,
                "project_id": 10,
                "file_name": "other_project.prt",
                "checked_out_by": None,
            },
        }
        self.members = {
            1: [{"child_cad_document_id": 2}],
            2: [{"child_cad_document_id": 3}],
            3: [{"child_cad_document_id": 1}],
        }

    def get_cad_document(self, document_id):
        row = self.documents.get(int(document_id))
        return dict(row) if row else None

    def list_cad_members(self, parent_id):
        return list(self.members.get(int(parent_id), []))

    def list_cad_documents(
        self,
        project_id,
        *,
        include_related_drawings=True,
        include_legacy_fallback=True,
    ):
        return [
            dict(row)
            for row in self.documents.values()
            if int(row.get("project_id") or 0) == int(project_id)
        ]

    def list_checked_out_cad_by_workspace(self, workspace_id):
        return [
            dict(row)
            for row in self.documents.values()
            if row.get("checked_out_by") is not None
            and str(row.get("checkout_workspace_id") or "") == str(workspace_id)
        ]


class _WorkspaceService:
    machine_id = "test-machine"

    def __init__(self):
        self.calls = []

    def get_workspace(self, workspace_id):
        return {
            "id": str(workspace_id),
            "name": "Creo Workspace",
            "path": "C:/workspace",
            "available": True,
        }

    def scan_workspace(self, workspace_id, project_id, user_id):
        return [{
            "workspace_id": str(workspace_id),
            "workspace_name": "Creo Workspace",
            "cad_document_id": 1,
            "project_id": int(project_id),
            "logical_file_name": "machine.asm",
            "filename": "machine.asm.7",
            "path": "C:/workspace/machine.asm.7",
            "modified": True,
            "status": "READY",
            "detail": "Modified and ready to stage.",
            "selectable": True,
        }]

    def materialize_cad_document_package(
        self,
        workspace_id,
        document_id,
        *,
        preserve_existing,
        include_related_drawings,
        editable,
    ):
        self.calls.append((int(document_id), bool(editable)))
        return [{
            "cad_document_id": int(document_id),
            "path": f"C:/workspace/{document_id}",
        }]


class CreoBridgeTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.connection_file = Path(self.temp.name) / "bridge.json"
        self.bridge = NexusCreoBridge(
            controller=_Controller(),
            port=0,
            connection_file=self.connection_file,
        )
        self.descriptor = self.bridge.start()
        self.addCleanup(self.bridge.stop)

    @staticmethod
    def _read_json(response):
        return json.loads(response.read().decode("utf-8"))

    def test_health_is_local_and_connection_descriptor_is_written(self):
        with urllib.request.urlopen(
            self.descriptor["api_url"] + "/health", timeout=2
        ) as response:
            payload = self._read_json(response)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["service"], "nexus-creo-bridge")

        saved = json.loads(self.connection_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["api_url"], self.descriptor["api_url"])
        self.assertEqual(saved["token"], self.descriptor["token"])

    def test_api_rejects_missing_token_and_accepts_current_token(self):
        url = self.descriptor["api_url"] + "/context"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(url, timeout=2)
        self.assertEqual(caught.exception.code, 401)

        request = urllib.request.Request(
            url, headers={"X-Nexus-Token": self.descriptor["token"]}
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = self._read_json(response)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["path"], "/api/v1/context")

    def test_post_body_is_decoded_and_own_descriptor_is_removed_on_stop(self):
        request = urllib.request.Request(
            self.descriptor["api_url"] + "/workspaces",
            data=json.dumps({"name": "Creo work"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Nexus-Token": self.descriptor["token"],
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = self._read_json(response)
        self.assertEqual(payload["data"]["body"]["name"], "Creo work")

        self.bridge.stop()
        self.assertFalse(self.connection_file.exists())

    def test_failed_connection_descriptor_write_stops_server_thread(self):
        blocked_parent = Path(self.temp.name) / "not-a-directory"
        blocked_parent.write_text("file", encoding="utf-8")
        bridge = NexusCreoBridge(
            controller=_Controller(),
            port=0,
            connection_file=blocked_parent / "bridge.json",
        )

        with self.assertRaises(OSError):
            bridge.start()

        self.assertEqual(bridge.api_url, "")
        self.assertIsNone(bridge._server)
        self.assertIsNone(bridge._thread)


class CreoBridgeControllerTests(unittest.TestCase):
    def setUp(self):
        self.repo = _CadRepo()
        pdm = SimpleNamespace(repo=self.repo)
        self.controller = CreoBridgeController(
            session=SimpleNamespace(user_id=7, project_id=9),
            project_service=SimpleNamespace(
                get_project_by_id=lambda project_id: {
                    "id": int(project_id),
                    "name": "Machine",
                }
            ),
            permission_repo=SimpleNamespace(
                user_has_permission=lambda *_args: True,
            ),
            bom_service_factory=lambda: SimpleNamespace(),
            workspace_service_factory=lambda: _WorkspaceService(),
            pdm_service_factory=lambda: SimpleNamespace(
                repo=self.repo,
                list_cad_documents=self.repo.list_cad_documents,
            ),
        )

    def test_project_cad_documents_lists_only_active_project(self):
        result = self.controller.dispatch("GET", "/api/v1/cad", {}, {})

        self.assertEqual(
            [row["file_name"] for row in result["cad_documents"]],
            ["bracket.prt", "frame.asm", "machine.asm"],
        )
        self.assertTrue(all(row["managed"] for row in result["cad_documents"]))
        self.assertNotIn(
            "other_project.prt",
            [row["file_name"] for row in result["cad_documents"]],
        )

    def test_workspace_checkouts_lists_active_project_checkout_rows(self):
        self.repo.documents[5] = {
            "id": 5,
            "project_id": 10,
            "file_name": "foreign.asm",
            "checked_out_by": 7,
            "checkout_workspace_id": "workspace-one",
            "checkout_workspace_machine_id": "test-machine",
        }

        result = self.controller.dispatch(
            "GET", "/api/v1/workspaces/workspace-one/checkouts", {}, {}
        )

        self.assertEqual(
            [row["file_name"] for row in result["cad_documents"]],
            ["machine.asm"],
        )
        self.assertEqual(result["workspace"]["id"], "workspace-one")
        self.assertTrue(result["cad_documents"][0]["can_checkin"])
        self.assertEqual(
            result["local_files"][0]["filename"],
            "machine.asm.7",
        )
        self.assertTrue(result["local_files"][0]["selectable"])

    def test_history_route_returns_append_only_checkout_events(self):
        history = [{
            "action": "CHECKIN",
            "note": "Creo update",
            "workspace_name": "Creo Workspace",
        }]
        self.controller._pdm_service_factory = lambda: SimpleNamespace(
            repo=self.repo,
            list_cad_documents=self.repo.list_cad_documents,
            cad_checkout_history=lambda _cad_id: history,
        )

        result = self.controller.dispatch(
            "GET", "/api/v1/cad/1/history", {}, {}
        )

        self.assertEqual(result["history"], history)
        self.assertEqual(result["cad"]["id"], 1)

    def test_revision_and_release_routes_use_the_pdm_service(self):
        calls = []
        self.controller._bom_service_factory = lambda: SimpleNamespace(
            revise_pdm_cad_document=lambda cad_id: calls.append(("revise", cad_id)) or {"id": cad_id},
            release_pdm_cad_document=lambda cad_id: calls.append(("release", cad_id)) or {"id": cad_id},
        )

        revised = self.controller.dispatch("POST", "/api/v1/cad/2/revise", {}, {})
        released = self.controller.dispatch("POST", "/api/v1/cad/2/release", {}, {})

        self.assertEqual(calls, [("revise", 2), ("release", 2)])
        self.assertEqual(revised["revision"]["id"], 2)
        self.assertEqual(released["release"]["id"], 2)

    def test_materializes_recursive_dependencies_read_only_unless_owned_here(self):
        workspace_service = _WorkspaceService()
        workspace = {"id": "workspace-one", "path": "C:/workspace"}

        files, dependency_ids = self.controller._materialize_cad_package(
            workspace_service,
            workspace,
            1,
            7,
            include_related_drawings=False,
            include_dependencies=True,
        )

        self.assertEqual(dependency_ids, [2, 3])
        self.assertEqual(workspace_service.calls, [(1, True), (2, False), (3, False)])
        self.assertEqual([row["cad_document_id"] for row in files], [1, 2, 3])

    def test_checkout_is_not_editable_when_machine_does_not_match(self):
        workspace_service = _WorkspaceService()
        document = dict(self.repo.documents[1])
        document["checkout_workspace_machine_id"] = "another-machine"

        editable = self.controller._checked_out_in_workspace(
            document,
            7,
            {"id": "workspace-one"},
            workspace_service,
        )

        self.assertFalse(editable)

    def test_released_related_drawing_requires_cad_revision_confirmation(self):
        document = {
            "id": 1,
            "project_id": 9,
            "file_name": "machine.asm",
            "category": "ASSEMBLY",
            "lifecycle_state": "WIP",
        }
        drawing = {
            "id": 4,
            "project_id": 9,
            "file_name": "machine.drw",
            "category": "DRAWING",
            "lifecycle_state": "RELEASED",
        }
        repo = SimpleNamespace(list_related_drawings=lambda _cad_id: [drawing])
        self.controller._pdm_service_factory = lambda: SimpleNamespace(repo=repo)
        self.controller._require_commit_permission = lambda: (7, 9, {})
        self.controller._document = lambda _cad_id: (7, 9, {}, document)
        self.controller._workspace = lambda _workspace_id: (
            _WorkspaceService(),
            {"id": "workspace-one", "path": "C:/workspace"},
        )

        with self.assertRaises(BridgeApiError) as caught:
            self.controller.checkout(1, {"workspace_id": "workspace-one"})

        self.assertEqual(caught.exception.code, "cad_revision_required")
        self.assertEqual(caught.exception.details["cad_document_ids"], [4])


if __name__ == "__main__":
    unittest.main()
