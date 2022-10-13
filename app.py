from __future__ import annotations
import asyncio
import websockets
from typing import TYPE_CHECKING
import os
import signal

if TYPE_CHECKING:
    from websockets.legacy.server import WebSocketServerProtocol
    from .connect4 import PLAYER1, PLAYER2, Connect4

import logging
logging.basicConfig(format="%(message)s", level=logging.DEBUG)

import json

if not TYPE_CHECKING:
    from connect4 import PLAYER1, PLAYER2, Connect4


import secrets


JOIN: dict[str, tuple[Connect4, set[WebSocketServerProtocol]]] = {}
WATCH: dict[str, tuple[Connect4, set[WebSocketServerProtocol]]] = {}


async def start(websocket: WebSocketServerProtocol):
    # Initialize a Connect Four game, the set of WebSocket connections
    # receiving moves from this game, and secret access token.
    game = Connect4()
    connected = {websocket}

    join_key = secrets.token_urlsafe()
    watch_key = secrets.token_urlsafe()
    JOIN[join_key] = game, connected
    WATCH[watch_key] = game, connected

    try:
        # Send the secret access token to the browser of the first player,
        # where it'll be used for building a "join" link.
        event = {
            "type": "init",
            "join": join_key,
            "watch": watch_key
        }
        await websocket.send(json.dumps(event))

        logging.info(f"first player started game {id(game)}")
        await play(websocket, game, PLAYER1, connected)

    finally:
        del JOIN[join_key]


async def error(websocket: WebSocketServerProtocol, message):
    event = {
        "type": "error",
        "message": message,
    }
    await websocket.send(json.dumps(event))


async def join(websocket: WebSocketServerProtocol, join_key):
    # Find the Connect Four game.
    try:
        game, connected = JOIN[join_key]
    except KeyError:
        await error(websocket, "Game not found.")
        return

    # Register to receive moves from this game.
    connected.add(websocket)
    try:

        logging.info(f"second player joined game {id(game)}")
        await play(websocket, game, PLAYER2, connected)

    finally:
        connected.remove(websocket)


async def watch(websocket: WebSocketServerProtocol, watch_key: str):
    try:
        game, connected = WATCH[watch_key]
    except KeyError:
        await error(websocket, "Game not found.")
        return

    connected.add(websocket)
    await replay(websocket, game.moves.copy())
    try:
        logging.info(f'spectator joined game: {id(game)}')
        await websocket.wait_closed()
    finally:
        connected.remove(websocket)


async def replay(websocket: WebSocketServerProtocol, history: list[tuple[str, int, int]]):
    for move in history:
        await websocket.send(json.dumps(
            {"type": "play", "player": move[0], "column": move[1], "row": move[2]}
        ))


async def handler(websocket: WebSocketServerProtocol):
    # Receive and parse the "init" event from the UI.
    message = await websocket.recv()
    event = json.loads(message)
    assert event["type"] == "init"

    if "join" in event:
        # Second player joins an existing game.
        await join(websocket, event["join"])
    elif "watch" in event:
        # Spectator joins game
        await watch(websocket, event['watch'])
    else:
        # First player starts a new game.
        await start(websocket)


async def play(websocket: WebSocketServerProtocol, game: Connect4, player, connected: set[WebSocketServerProtocol]):
    async for message in websocket:
        data: dict[str, str | int] = json.loads(message)
        try:
            row = game.play(player, data['column'])
        except RuntimeError as e:
            await websocket.send(json.dumps({"type": "error", "message": str(e)}))
        else:
            if not game.winner:
                data_to_send = json.dumps(
                    {"type": "play", "player": player, "column": data["column"], "row": row}
                )
            else:
                data_to_send = json.dumps({"type": "win", "player": game.winner})
            websockets.broadcast(connected, data_to_send)


async def main():
    # Set the stop condition when receiving SIGTERM.
    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)

    port = int(os.environ.get("PORT", "8001"))
    async with websockets.serve(handler, "", port):
        await stop


if __name__ == "__main__":
    asyncio.run(main())
