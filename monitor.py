from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
from services.system import get_system_info

router = APIRouter()


@router.websocket("/ws/stats")
async def stats_websocket(websocket: WebSocket):
    """
    WebSocket endpoint that streams live system stats every 2 seconds.
    Clients receive JSON with CPU, RAM, disk, network, and load data.
    """
    await websocket.accept()
    try:
        while True:
            stats = get_system_info()
            await websocket.send_json(stats)
            await asyncio.sleep(2)
    except (WebSocketDisconnect, Exception):
        # Client disconnected or connection error — exit cleanly
        pass

