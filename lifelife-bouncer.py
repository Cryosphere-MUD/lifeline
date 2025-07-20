#!/usr/bin/python

import asyncio
import uuid
import json
from collections import deque
import struct
import time

MUD_HOST = "host.docker.internal"
MUD_PORT = 6666
BUFFER_LIMIT = 65536  # bytes
SESSION_TIMEOUT = 600  # seconds

TYPE_DATA = 0x00
TYPE_CTRL = 0x01

sessions = {}

def pack_frame(frame_type, payload: bytes) -> bytes:
    return struct.pack("!BH", frame_type, len(payload)) + payload

def unpack_frames(data: bytearray):
    frames = []
    while len(data) >= 3:
        frame_type, length = struct.unpack("!BH", data[:3])
        if len(data) < 3 + length:
            break
        payload = data[3:3 + length]
        frames.append((frame_type, payload))
        del data[:3 + length]
    return frames


class Session:
    def __init__(self, session_id):
        self.session_id = session_id
        self.buffer = deque()
        self.ack_offset = 0
        self.total_bytes = 0
        self.client_writer = None
        self.mud_reader = None
        self.mud_writer = None
        self.last_active = time.time()
        self.mud_task = None

    async def connect_to_mud(self):
        try:
            self.mud_reader, self.mud_writer = await asyncio.open_connection(MUD_HOST, MUD_PORT)
        except ConnectionRefusedError:
            pass
        self.mud_task = asyncio.create_task(self._read_mud())

    async def _read_mud(self):
        while True:
            data = await self.mud_reader.read(1024)
            if not data:
                await self._handle_mud_disconnect("Connection lost")
                break
            self._append_buffer(data)
            await self._send_to_client(TYPE_DATA, data)

    async def _send_to_client(self, frame_type, data):
        if self.client_writer:
            print("sending", len(data), "data bytes", data)
            self.client_writer.write(pack_frame(frame_type, data))
            await self.client_writer.drain()

    def _append_buffer(self, data):
        self.total_bytes += len(data)
        self.buffer.append(data)
        while sum(len(chunk) for chunk in self.buffer) > BUFFER_LIMIT:
            self.buffer.popleft()

    async def attach_client(self, reader, writer, resume_offset=None):
        self.client_writer = writer
        self.last_active = time.time()

        if self.mud_reader is None or self.mud_writer is None:
            print("refused!")
            await self._handle_mud_disconnect("Connection refused")
            return

        # Send session token as control frame
        await self._send_to_client(TYPE_CTRL, json.dumps({"session": self.session_id}).encode())

        # Replay buffer if resuming
        if resume_offset is not None:
          if resume_offset < self.total_bytes:
            skip = resume_offset - (self.total_bytes - sum(len(x) for x in self.buffer))
            for chunk in self.buffer:
                if skip >= len(chunk):
                    skip -= len(chunk)
                    continue
                await self._send_to_client(TYPE_DATA, chunk[skip:])
                skip = 0
        else:
            # Full buffer replay
            for chunk in self.buffer:
                await self._send_to_client(TYPE_DATA, chunk)

        asyncio.create_task(self._read_client(reader))

    async def _read_client(self, reader):
        buf = bytearray()
        while True:
            data = await reader.read(1024)
            if not data:
                self.client_writer = None
                break
            buf.extend(data)
            for frame_type, payload in unpack_frames(buf):
                if frame_type == TYPE_DATA:
                    # Send to MUD
                    self.mud_writer.write(payload)
                    await self.mud_writer.drain()
                elif frame_type == TYPE_CTRL:
                    self._handle_control(payload)

    async def _handle_mud_disconnect(self, error):
                # Notify the client that the connection to the MUD has been lost
                if self.client_writer:
                        try:
                                await self._send_to_client(TYPE_CTRL, json.dumps({"error": error}).encode())
                        except Exception:
                                pass
                        self.client_writer.close()
                # Cleanup mud_writer
                if self.mud_writer:
                        self.mud_writer.close()
                        self.mud_writer = None
                        
    def _handle_control(self, payload: bytes):
        try:
            msg = json.loads(payload.decode())
        except Exception:
            return
        if "ack" in msg:
            print(msg)
            self.ack_offset = msg["ack"]
            # Drop data before ack_offset
            self._trim_buffer()
        self.last_active = time.time()

    def _trim_buffer(self):
        current_offset = self.total_bytes - sum(len(c) for c in self.buffer)
        while self.buffer and current_offset + len(self.buffer[0]) <= self.ack_offset:
            current_offset += len(self.buffer.popleft())


async def handle_client(reader, writer):
    data = await reader.read(1024)

    # First frame must be control with either { "new": true } or { "resume": <token> }
    buf = bytearray(data)

    frames = unpack_frames(buf)
    if not frames or frames[0][0] != TYPE_CTRL:
        writer.write(pack_frame(TYPE_CTRL, b'{"error":"Missing control frame"}'))
        await writer.drain()
        writer.close()
        return

    msg = json.loads(frames[0][1].decode())
    session = None

    if "resume" in msg:
        token = msg["resume"]
        session = sessions.get(token)
        if session:
            await session.attach_client(reader, writer, resume_offset=msg.get("ack"))
            return
        else:
            writer.write(pack_frame(TYPE_CTRL, b'{"error":"Invalid session"}'))
            await writer.drain()
            writer.close()
            return
    else:
        session_id = uuid.uuid4().hex
        session = Session(session_id)
        sessions[session_id] = session
        await session.connect_to_mud()
        await session.attach_client(reader, writer)


async def cleanup_sessions():
    while True:
        now = time.time()
        to_remove = [sid for sid, s in sessions.items() if now - s.last_active > SESSION_TIMEOUT]
        for sid in to_remove:
            sess = sessions.pop(sid)
            if sess.mud_writer:
                sess.mud_writer.close()
        await asyncio.sleep(30)


async def main():
    server = await asyncio.start_server(handle_client, "0.0.0.0", 12345)
    asyncio.create_task(cleanup_sessions())
    async with server:
        await server.serve_forever()

print("starting bouncer")

if __name__ == "__main__":
    asyncio.run(main())
