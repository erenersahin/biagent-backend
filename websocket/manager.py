"""
WebSocket Connection Manager

Manages WebSocket connections and broadcasts messages to clients.
"""

import asyncio
import json
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from datetime import datetime

from config import settings


router = APIRouter()


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


async def broadcast_message(message: dict):
    """Broadcast a message to all connected clients."""
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
                # Handle reconnection
                session_id = data.get("session_id")
                await websocket.send_json({
                    "type": "reconnected",
                    "session_id": session_id,
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
