"""
Remote MCP (Model Context Protocol) Server for Google Firestore Task Management.

Built using FastMCP and firebase-admin.
Designed to run over HTTP/SSE transport (0.0.0.0:8000), exposed via Cloudflare Tunnel,
and connected to Gemini Custom Connected Apps or any MCP-compatible client.
"""

import os
import sys
from datetime import datetime
from typing import Optional, Literal

import firebase_admin
from firebase_admin import credentials, firestore
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Firebase & Firestore Initialization
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_KEY_PATH = os.environ.get(
    "FIREBASE_SERVICE_ACCOUNT_KEY",
    os.path.join(BASE_DIR, "serviceAccountKey.json"),
)

_firestore_client: Optional[firestore.Client] = None


def get_db() -> firestore.Client:
    """
    Retrieves or initializes the Firestore database client singleton.
    
    Loads credentials from the local 'serviceAccountKey.json' file or the path
    specified by the FIREBASE_SERVICE_ACCOUNT_KEY environment variable.
    """
    global _firestore_client
    if _firestore_client is not None:
        return _firestore_client

    if not os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
        raise FileNotFoundError(
            f"Firebase service account file not found at: '{SERVICE_ACCOUNT_KEY_PATH}'.\n"
            "Please download your service account key from the Firebase Console:\n"
            "  Project Settings > Service accounts > Generate new private key\n"
            "and place it as 'serviceAccountKey.json' in the project root directory."
        )

    if not firebase_admin._apps:
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred)

    _firestore_client = firestore.client()
    return _firestore_client


# Eagerly initialize Firebase client on startup if the credential file exists
try:
    if os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
        get_db()
        print(f"[Firebase] Successfully connected to Firestore using {SERVICE_ACCOUNT_KEY_PATH}")
    else:
        print(
            f"[Firebase] Note: '{SERVICE_ACCOUNT_KEY_PATH}' not found yet. "
            "Server will start, but database operations will require this file.",
            file=sys.stderr,
        )
except Exception as e:
    print(f"[Firebase] Initialization warning: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# FastMCP Server Setup
# ---------------------------------------------------------------------------
# FastMCP instance configured for remote SSE transport on 0.0.0.0:8000
mcp = FastMCP("GeminiTaskMCP", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
mcp.settings.host = "0.0.0.0"
mcp.settings.port = 8000


# ---------------------------------------------------------------------------
# MCP Tools (Firestore "tasks" collection)
# ---------------------------------------------------------------------------

@mcp.tool()
def add_task(title: str, priority: str = "Normal") -> str:
    """
    Adds a new task to the Firestore 'tasks' collection.

    Parameters:
        title (str): The descriptive title or description of the task to be created.
        priority (str): Task priority level. Must be one of 'High', 'Medium', or 'Normal'. Defaults to 'Normal'.

    Returns:
        str: Confirmation message including the newly generated Firestore Document ID, title, and priority.
    """
    try:
        clean_title = (title or "").strip()
        if not clean_title:
            return "Error: Task 'title' cannot be empty."

        clean_priority = (priority or "").strip().capitalize()
        valid_priorities = {"High", "Medium", "Normal"}
        if clean_priority not in valid_priorities:
            return (
                f"Error: Invalid priority '{priority}'. "
                f"Allowed values are: 'High', 'Medium', or 'Normal' (default: 'Normal')."
            )

        db = get_db()
        task_data = {
            "title": clean_title,
            "priority": clean_priority,
            "completed": False,
            "created_at": firestore.SERVER_TIMESTAMP,
        }

        _, doc_ref = db.collection("tasks").add(task_data)
        return (
            f"Task created successfully!\n"
            f"- Document ID: {doc_ref.id}\n"
            f"- Title: {clean_title}\n"
            f"- Priority: {clean_priority}\n"
            f"- Status: Pending (completed: False)"
        )
    except FileNotFoundError as fnf_err:
        return f"Configuration Error: {fnf_err}"
    except Exception as exc:
        return f"Error adding task: {str(exc)}"


@mcp.tool()
def get_tasks(status: str = "all") -> str:
    """
    Retrieves tasks from the Firestore 'tasks' collection ordered by creation date (newest first).

    Parameters:
        status (str): Filter tasks by completion status.
                      Options: 'all' (default, return all tasks),
                               'pending' (only uncompleted tasks),
                               'completed' (only finished tasks).

    Returns:
        str: Clean, readable formatted list showing each task's Document ID, Title, Priority, Status, and Created Time.
    """
    try:
        clean_status = (status or "all").strip().lower()
        if clean_status not in {"all", "pending", "completed"}:
            return (
                f"Error: Invalid status filter '{status}'. "
                f"Allowed options are: 'all', 'pending', or 'completed'."
            )

        db = get_db()
        tasks_ref = db.collection("tasks")

        # Query tasks ordered by creation date descending
        try:
            docs = list(tasks_ref.order_by("created_at", direction=firestore.Query.DESCENDING).stream())
        except Exception:
            # Fallback in case of pending indexes or documents without timestamps
            docs = list(tasks_ref.stream())

        # Python-side sort to ensure newest tasks are always first
        def extract_timestamp(doc_snapshot):
            data = doc_snapshot.to_dict() or {}
            ts = data.get("created_at")
            if isinstance(ts, datetime):
                return ts.timestamp()
            return 0.0

        docs.sort(key=extract_timestamp, reverse=True)

        matching_tasks = []
        for doc in docs:
            data = doc.to_dict() or {}
            is_completed = bool(data.get("completed", False))

            if clean_status == "pending" and is_completed:
                continue
            if clean_status == "completed" and not is_completed:
                continue

            matching_tasks.append((doc.id, data, is_completed))

        if not matching_tasks:
            return f"No tasks found matching status: '{clean_status}'."

        lines = [f"Found {len(matching_tasks)} task(s) [Filter: {clean_status}]:\n"]
        for idx, (doc_id, data, is_completed) in enumerate(matching_tasks, start=1):
            t_title = data.get("title", "(Untitled)")
            t_priority = data.get("priority", "Normal")
            t_status = "Completed" if is_completed else "Pending"

            ts = data.get("created_at")
            if isinstance(ts, datetime):
                created_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
            else:
                created_str = "Recently added"

            lines.append(
                f"{idx}. [ID: {doc_id}] \"{t_title}\"\n"
                f"   - Priority: {t_priority}\n"
                f"   - Status: {t_status}\n"
                f"   - Created At: {created_str}"
            )

        return "\n\n".join(lines)
    except FileNotFoundError as fnf_err:
        return f"Configuration Error: {fnf_err}"
    except Exception as exc:
        return f"Error retrieving tasks: {str(exc)}"


@mcp.tool()
def complete_task(task_id: str) -> str:
    """
    Marks an existing task in the Firestore 'tasks' collection as completed.

    Parameters:
        task_id (str): The Firestore Document ID of the task to mark as completed.

    Returns:
        str: Confirmation message if successfully completed, or an error if the Task ID is not found.
    """
    try:
        clean_id = (task_id or "").strip()
        if not clean_id:
            return "Error: 'task_id' cannot be empty."

        db = get_db()
        doc_ref = db.collection("tasks").document(clean_id)
        doc = doc_ref.get()

        if not doc.exists:
            return f"Error: Task ID '{clean_id}' not found in 'tasks' collection."

        task_data = doc.to_dict() or {}
        title = task_data.get("title", "(Untitled)")

        if task_data.get("completed") is True:
            return f"Task '{clean_id}' (\"{title}\") is already marked as completed."

        doc_ref.update({"completed": True})
        return f"Success: Task '{clean_id}' (\"{title}\") has been marked as completed."
    except FileNotFoundError as fnf_err:
        return f"Configuration Error: {fnf_err}"
    except Exception as exc:
        return f"Error completing task '{task_id}': {str(exc)}"


@mcp.tool()
def delete_task(task_id: str) -> str:
    """
    Deletes a task document by ID from the Firestore 'tasks' collection.

    Parameters:
        task_id (str): The Firestore Document ID of the task to delete.

    Returns:
        str: Confirmation message indicating the task was successfully deleted, or an error if not found.
    """
    try:
        clean_id = (task_id or "").strip()
        if not clean_id:
            return "Error: 'task_id' cannot be empty."

        db = get_db()
        doc_ref = db.collection("tasks").document(clean_id)
        doc = doc_ref.get()

        if not doc.exists:
            return f"Error: Task ID '{clean_id}' not found in 'tasks' collection."

        task_data = doc.to_dict() or {}
        title = task_data.get("title", "(Untitled)")

        doc_ref.delete()
        return f"Success: Task '{clean_id}' (\"{title}\") has been permanently deleted from 'tasks'."
    except FileNotFoundError as fnf_err:
        return f"Configuration Error: {fnf_err}"
    except Exception as exc:
        return f"Error deleting task '{task_id}': {str(exc)}"


# ---------------------------------------------------------------------------
# Server Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 70)
    print("  🚀 GeminiTaskMCP: Remote Firestore MCP Server Starting")
    print("=" * 70)
    print("  • Transport:   SSE (Server-Sent Events) / HTTP")
    print("  • Host/Port:   http://0.0.0.0:8000")
    print("  • SSE Stream:  http://0.0.0.0:8000/sse")
    print("  • Messages:    http://0.0.0.0:8000/messages/")
    print("  • Collection:  'tasks'")
    print("-" * 70)
    print("  Public Tunnel Command (Cloudflare):")
    print("    npx cloudflared tunnel --url http://localhost:8000")
    print("=" * 70)
    
    # Run the FastMCP server over SSE transport
    mcp.run(transport="streamable-http")
