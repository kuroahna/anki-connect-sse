import aqt


anki_version = tuple(int(segment) for segment in aqt.appVersion.split("."))
if anki_version < (2, 1, 54):
    raise Exception("Minimum Anki version supported: 2.1.54")


import anki
import threading
import json
import socket

from http.server import BaseHTTPRequestHandler, HTTPServer
from aqt.qt import QAction


has_started = False
anki_add_note = None
anki_remove_notes = None
anki_update_note = None
collection = None
connections = set()


def add_note(self, note, deck_id):
    changes = anki_add_note(self, note, deck_id)
    broadcast_add_note(note)
    return changes


def remove_notes(self, note_ids):
    broadcast_remove_notes(note_ids)
    return anki_remove_notes(self, note_ids)


def update_note(self, note):
    # The note passed in is already updated with the new changes, so we should
    # grab the note from the database for the current state
    broadcast_remove_notes([note.id])
    changes = anki_update_note(self, note)
    broadcast_add_note(note)
    return changes


def broadcast_add_note(note):
    first_field_value = note.fields[0]
    value = {"type": "add", "query": first_field_value, "noteId": note.id}
    broadcast(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def broadcast_remove_notes(note_ids):
    for note_id in note_ids:
        note = collection.get_note(note_id)
        first_field_value = note.fields[0]
        value = {"type": "remove", "query": first_field_value, "noteId": note_id}
        broadcast(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def broadcast(message):
    global connections
    for connection in connections.copy():
        try:
            connection.sendall(f"data: {message}\n\n".encode("utf-8"))
        except socket.error:
            connection.close()
            connections.remove(connection)


class SSEHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Disable logging so that Anki does not throw an error
        pass

    def do_GET(self):
        global connections
        connections.add(self.connection)

        self.send_response(200)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        note_ids = collection.db.list("select id from notes")
        for note_id in note_ids:
            note = collection.get_note(note_id)
            first_field_value = note.fields[0]
            value = {"type": "add", "query": first_field_value, "noteId": note_id}
            self.send_data(json.dumps(value, separators=(",", ":"), ensure_ascii=False))

    def send_data(self, data):
        message = f"data: {data}\n\n"
        self.wfile.write(message.encode())


def start_sse_server(name):
    server_address = ("", 12345)
    httpd = HTTPServer(server_address, SSEHandler)
    print("SSE server listening on port 12345...")
    httpd.serve_forever()


def start_server():
    global has_started
    if has_started:
        print("SSE server has already started")
        return

    # Patch anki add, update, and remove notes functions
    global anki_add_note
    global anki_remove_notes
    global anki_update_note
    global collection
    anki_add_note = anki.Collection.add_note
    anki_remove_notes = anki.Collection.remove_notes
    anki_update_note = anki.Collection.update_note
    anki.Collection.add_note = add_note
    anki.Collection.remove_notes = remove_notes
    anki.Collection.update_note = update_note

    collection = aqt.mw.col
    if collection is None:
        raise Exception("collection is not available")

    thread = threading.Thread(target=start_sse_server, daemon=True, args=(1,))
    thread.start()
    has_started = True


if __name__ != "plugin":
    # Add a menu item to start the server because we need to wait for Anki to
    # be fully loaded to have access to the collection
    action = QAction(
        "Start AnkiConnect Server-Sent Event server",
        aqt.mw,
        triggered=start_server,
    )
    aqt.mw.form.menuTools.addAction(action)
