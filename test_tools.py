"""
Unit test suite for server.py MCP tools and logic.
Uses mocks to verify behavior without requiring a live Firebase connection.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

# ---------------------------------------------------------------------------
# Mock external dependencies before server import if not installed
# ---------------------------------------------------------------------------
for mod_name in ["mcp", "mcp.server", "mcp.server.fastmcp", "firebase_admin", "firebase_admin.credentials", "firebase_admin.firestore"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Configure FastMCP mock decorator to return the function itself
def mock_tool():
    def decorator(fn):
        return fn
    return decorator

sys.modules["mcp.server.fastmcp"].FastMCP.return_value.tool = mock_tool

# Now import server functions
import server


class TestGeminiTaskMCP(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_collection = MagicMock()
        self.mock_db.collection.return_value = self.mock_collection
        server._firestore_client = self.mock_db

    # -----------------------------------------------------------------------
    # 1. add_task tests
    # -----------------------------------------------------------------------
    def test_add_task_success_default_priority(self):
        mock_doc_ref = MagicMock()
        mock_doc_ref.id = "test_doc_123"
        self.mock_collection.add.return_value = (None, mock_doc_ref)

        result = server.add_task(title="Deploy MCP Server")
        self.assertIn("Task created successfully!", result)
        self.assertIn("test_doc_123", result)
        self.assertIn("Priority: Normal", result)

        # Check call arguments
        self.mock_collection.add.assert_called_once()
        added_data = self.mock_collection.add.call_args[0][0]
        self.assertEqual(added_data["title"], "Deploy MCP Server")
        self.assertEqual(added_data["priority"], "Normal")
        self.assertFalse(added_data["completed"])

    def test_add_task_success_custom_priority(self):
        mock_doc_ref = MagicMock()
        mock_doc_ref.id = "doc_high_456"
        self.mock_collection.add.return_value = (None, mock_doc_ref)

        result = server.add_task(title="Fix Critical Bug", priority="high")
        self.assertIn("doc_high_456", result)
        self.assertIn("Priority: High", result)

    def test_add_task_empty_title_error(self):
        result = server.add_task(title="   ")
        self.assertIn("Error: Task 'title' cannot be empty.", result)

    def test_add_task_invalid_priority_error(self):
        result = server.add_task(title="Check email", priority="Urgent")
        self.assertIn("Error: Invalid priority 'Urgent'", result)

    # -----------------------------------------------------------------------
    # 2. get_tasks tests
    # -----------------------------------------------------------------------
    def test_get_tasks_invalid_status_error(self):
        result = server.get_tasks(status="unknown_status")
        self.assertIn("Error: Invalid status filter 'unknown_status'", result)

    def test_get_tasks_empty(self):
        mock_stream = MagicMock()
        mock_stream.stream.return_value = []
        self.mock_collection.order_by.return_value = mock_stream

        result = server.get_tasks(status="all")
        self.assertIn("No tasks found matching status: 'all'.", result)

    def test_get_tasks_filtering(self):
        doc1 = MagicMock()
        doc1.id = "task_1"
        doc1.to_dict.return_value = {
            "title": "Task One",
            "priority": "High",
            "completed": False,
            "created_at": datetime(2026, 11, 16, 10, 0, 0),
        }

        doc2 = MagicMock()
        doc2.id = "task_2"
        doc2.to_dict.return_value = {
            "title": "Task Two",
            "priority": "Normal",
            "completed": True,
            "created_at": datetime(2026, 11, 16, 9, 0, 0),
        }

        mock_stream = MagicMock()
        mock_stream.stream.return_value = [doc1, doc2]
        self.mock_collection.order_by.return_value = mock_stream

        # Test 'all'
        res_all = server.get_tasks(status="all")
        self.assertIn("Found 2 task(s)", res_all)
        self.assertIn("task_1", res_all)
        self.assertIn("task_2", res_all)

        # Test 'pending'
        res_pending = server.get_tasks(status="pending")
        self.assertIn("Found 1 task(s)", res_pending)
        self.assertIn("task_1", res_pending)
        self.assertNotIn("task_2", res_pending)

        # Test 'completed'
        res_completed = server.get_tasks(status="completed")
        self.assertIn("Found 1 task(s)", res_completed)
        self.assertNotIn("task_1", res_completed)
        self.assertIn("task_2", res_completed)

    # -----------------------------------------------------------------------
    # 3. complete_task tests
    # -----------------------------------------------------------------------
    def test_complete_task_empty_id(self):
        result = server.complete_task("  ")
        self.assertIn("Error: 'task_id' cannot be empty.", result)

    def test_complete_task_not_found(self):
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = False
        mock_doc_ref.get.return_value = mock_snapshot
        self.mock_collection.document.return_value = mock_doc_ref

        result = server.complete_task("missing_id")
        self.assertIn("Error: Task ID 'missing_id' not found", result)

    def test_complete_task_success(self):
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {"title": "Update Documentation", "completed": False}
        mock_doc_ref.get.return_value = mock_snapshot
        self.mock_collection.document.return_value = mock_doc_ref

        result = server.complete_task("doc_xyz")
        self.assertIn("Success: Task 'doc_xyz' (\"Update Documentation\") has been marked as completed.", result)
        mock_doc_ref.update.assert_called_once_with({"completed": True})

    # -----------------------------------------------------------------------
    # 4. delete_task tests
    # -----------------------------------------------------------------------
    def test_delete_task_empty_id(self):
        result = server.delete_task("  ")
        self.assertIn("Error: 'task_id' cannot be empty.", result)

    def test_delete_task_not_found(self):
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = False
        mock_doc_ref.get.return_value = mock_snapshot
        self.mock_collection.document.return_value = mock_doc_ref

        result = server.delete_task("missing_id")
        self.assertIn("Error: Task ID 'missing_id' not found", result)

    def test_delete_task_success(self):
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {"title": "Outdated Task"}
        mock_doc_ref.get.return_value = mock_snapshot
        self.mock_collection.document.return_value = mock_doc_ref

        result = server.delete_task("task_to_del")
        self.assertIn("Success: Task 'task_to_del' (\"Outdated Task\") has been permanently deleted", result)
        mock_doc_ref.delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
