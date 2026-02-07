"""
WebSocket Connection Manager

Manages WebSocket connections and broadcasts messages to clients.
Supports token buffering for reconnection catchup.
"""

import asyncio
import json
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from datetime import datetime

from config import settings
from db import get_db, generate_id
from services import session_store


router = APIRouter()

# Event types that should be stored for offline replay
OFFLINE_EVENT_TYPES = {
    "step_completed",
    "pipeline_completed",
    "pipeline_failed",
    "pipeline_paused",
    "pr_approved",
    "changes_requested",
    "review_received",
    "waiting_for_review",
    "subagent_tool_call",  # Real-time subagent tool calls for persistence
}


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, client_id: str):
        """Accept and register a new connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections[client_id] = websocket

    async def disconnect(self, client_id: str):
        """Remove a connection."""
        async with self._lock:
            if client_id in self.active_connections:
                del self.active_connections[client_id]

    async def send_personal_message(self, message: dict, client_id: str):
        """Send a message to a specific client."""
        async with self._lock:
            websocket = self.active_connections.get(client_id)
            if websocket:
                try:
                    await websocket.send_json(message)
                except Exception:
                    # Connection might be closed
                    pass

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        async with self._lock:
            disconnected = []
            for client_id, websocket in self.active_connections.items():
                try:
                    await websocket.send_json(message)
                except Exception:
                    disconnected.append(client_id)

            # Clean up disconnected clients
            for client_id in disconnected:
                del self.active_connections[client_id]

    @property
    def connection_count(self) -> int:
        """Get number of active connections."""
        return len(self.active_connections)


# Global manager instance
manager = ConnectionManager()


async def store_offline_event(message: dict):
    """Store an important event for offline replay.

    Events are stored per-pipeline so sessions can retrieve them on reconnect.
    """
    event_type = message.get("type", "")
    pipeline_id = message.get("pipeline_id")

    if event_type not in OFFLINE_EVENT_TYPES or not pipeline_id:
        return

    try:
        db = await get_db()

        # Find all sessions that have this pipeline open in a tab
        sessions = await db.fetchall("""
            SELECT DISTINCT s.id as session_id
            FROM sessions s
            JOIN session_tabs st ON s.id = st.session_id
            WHERE st.pipeline_id = ?
        """, (pipeline_id,))

        now = datetime.utcnow().isoformat()

        for session in sessions:
            event_id = generate_id()
            await db.execute("""
                INSERT INTO offline_events (id, session_id, pipeline_id, event_type, event_data, created_at, acknowledged)
                VALUES (?, ?, ?, ?, ?, ?, FALSE)
            """, (
                event_id,
                session["session_id"],
                pipeline_id,
                event_type,
                json.dumps(message),
                now,
            ))

        await db.commit()
    except Exception as e:
        print(f"[OFFLINE_EVENTS] Error storing event: {e}")


async def broadcast_message(message: dict):
    """Broadcast a message to all connected clients and store for offline replay."""
    # Store important events for offline replay
    await store_offline_event(message)

    # Broadcast to connected clients
    await manager.broadcast(message)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, client_id: Optional[str] = None):
    """WebSocket endpoint for real-time updates."""
    # Generate client ID if not provided
    if not client_id:
        client_id = f"client_{datetime.utcnow().timestamp()}"

    await manager.connect(websocket, client_id)

    # Send connection confirmation
    await websocket.send_json({
        "type": "connected",
        "client_id": client_id,
        "timestamp": datetime.utcnow().isoformat(),
    })

    try:
        while True:
            # Receive and process messages from client
            data = await websocket.receive_json()
            message_type = data.get("type", "")

            if message_type == "ping":
                # Respond to heartbeat
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat(),
                })

            elif message_type == "subscribe":
                # Subscribe to specific pipeline updates
                pipeline_id = data.get("pipeline_id")
                if pipeline_id:
                    await websocket.send_json({
                        "type": "subscribed",
                        "pipeline_id": pipeline_id,
                    })

            elif message_type == "client_reconnecting":
                # Handle reconnection with token catchup
                session_id = data.get("session_id")
                pipeline_id = data.get("pipeline_id")
                last_step = data.get("last_step", 1)
                last_timestamp = data.get("last_timestamp", datetime.utcnow().isoformat())

                catchup_data = {}
                if pipeline_id:
                    catchup_data = await session_store.get_reconnection_data(
                        pipeline_id,
                        last_step,
                        last_timestamp
                    )

                await websocket.send_json({
                    "type": "reconnected",
                    "session_id": session_id,
                    "catchup": catchup_data,
                })

            elif message_type == "get_missed_events":
                # Get offline events for a session
                session_id = data.get("session_id")
                if session_id:
                    db = await get_db()
                    events = await db.fetchall("""
                        SELECT * FROM offline_events
                        WHERE session_id = ? AND acknowledged = FALSE
                        ORDER BY created_at ASC
                    """, (session_id,))

                    await websocket.send_json({
                        "type": "missed_events",
                        "events": [
                            {
                                "id": e["id"],
                                "type": e["event_type"],
                                "pipeline_id": e["pipeline_id"],
                                "data": json.loads(e["event_data"]) if e["event_data"] else {},
                                "created_at": e["created_at"],
                            }
                            for e in events
                        ],
                    })

            elif message_type == "acknowledge_events":
                # Mark events as acknowledged
                event_ids = data.get("event_ids", [])
                if event_ids:
                    db = await get_db()
                    placeholders = ",".join("?" * len(event_ids))
                    await db.execute(f"""
                        UPDATE offline_events SET acknowledged = TRUE
                        WHERE id IN ({placeholders})
                    """, event_ids)
                    await db.commit()
                    await websocket.send_json({
                        "type": "events_acknowledged",
                        "count": len(event_ids),
                    })

            elif message_type == "client_disconnecting":
                # Graceful disconnect
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await manager.disconnect(client_id)


@router.websocket("/ws/{pipeline_id}")
async def pipeline_websocket(websocket: WebSocket, pipeline_id: str):
    """WebSocket endpoint for specific pipeline updates."""
    client_id = f"pipeline_{pipeline_id}_{datetime.utcnow().timestamp()}"
    await manager.connect(websocket, client_id)

    await websocket.send_json({
        "type": "connected",
        "client_id": client_id,
        "pipeline_id": pipeline_id,
        "timestamp": datetime.utcnow().isoformat(),
    })

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat(),
                })

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.disconnect(client_id)
