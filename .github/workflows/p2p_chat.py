# Imported packages
import os
import sys
import json
import queue
import base64
import socket
import hashlib
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


# Defines the main Peer class that represents each node in the P2P network.
class Peer:
    # Initializes a new peer with a username, host address, and port number.
    def __init__(self, username, host='127.0.0.1', port=5000):
        self.username = username
        self.host = host
        self.port = port

        # Generates unique keys based on host and port, and username histories are mapped to those keys.
        self.key = str(self.hash_identity(self.host, self.port))
        self.username_history = {username: self.key}

        # Creates callback for GUI output and events.
        self.gui_callback = None
        self.gui_event_callback = None

        # Creates a server socket for incoming connections, with a pending limit of 5.
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)

        # Creates a distributed hash table (DHT) to store peer information.
        self.dht = {self.key: {"username": self.username,"host": self.host,"port": self.port,"status": "Online"}}

        # Creates a lock for thread-safe updates to shared data.
        self.lock = threading.Lock()

    # Static method to generate a hash identity from host and port.
    @staticmethod
    def hash_identity(host, port):
        # Creates a string representation of the peer's identity.
        identity_str = f"{host}:{port}"
        # Generates a SHA1 hash and converts it to an integer.
        return int(hashlib.sha1(identity_str.encode()).hexdigest(), 16)

    # Starts the peer's server in a separate thread.
    def start_server(self):
        threading.Thread(target=self.listen_for_connections, daemon=True).start()

    # Listens for incoming socket connections.
    def listen_for_connections(self):
        self._output(f"[INFO] Listening on {self.host}:{self.port}.")

        while True:
            try:
                # Accepts a new connection to handle in a separate thread.
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_connection, args=(conn, addr), daemon=True).start()
            except Exception as e:
                self._output(f"[ERROR] Error accepting connections: {e}")

    # Handles an individual connection from another peer.
    def handle_connection(self, conn, addr):
        while True:
            # Allows up to 4096 bytes of data to be received.
            data = conn.recv(4096)

            # Breaks the loop if no data is received.
            if not data:
                break

            # Decodes the data from bytes into a string and loads the JSON message before processing it.
            message = json.loads(data.decode('utf-8'))
            self.process_message(message, conn)

        # Closes the connection when done.
        conn.close()

    # Broadcasts a peer list message to all connected peers.
    def broadcast_peer_list(self):
        with self.lock:
            # Prepares a message with the current DHT (peer list).
            peer_list_message = {"type": "peer_list", "peers": self.dht}

            # Loops through each peer in the DHT.
            for peer_key, info in list(self.dht.items()):
                # Skips sending to self.
                if peer_key == self.key:
                    continue

                try:
                    # Creates a new socket to connect to a peer, sends an updated peer message, then closes the socket.
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect((info["host"], info["port"]))
                    s.send(json.dumps(peer_list_message).encode('utf-8'))
                    s.close()
                except Exception as e:
                    self._output(f"[ERROR] Could not send updated peer list to {info.get('username', 'Unknown')} - {e}")

    # Processes a received message based on its type.
    def process_message(self, message, conn=None):
        # Gets the message type.
        msg_type = message.get("type")

        # Processes control messages (join, update, leave, and open_chat_window).
        if msg_type == "control":
            # Gets the specific control type.
            control_type = message.get("control")

            # Handles join control messages.
            if control_type == "join":
                # Extracts details from the new peer.
                new_username = message.get("sender")
                new_peer_key = str(message.get("peer_key"))
                new_host = message.get("host")
                new_port = message.get("port")

                with self.lock:
                    # Updates the DHT and username history with the new peer's information.
                    self.dht[new_peer_key] =    {
                                                "username": new_username,
                                                "host": new_host,
                                                "port": new_port,
                                                "status": "Online"
                                                }
                    self.username_history[new_username] = new_peer_key

                # Broadcasts the arrival and updated list.
                self._output(f"[INFO] {new_username} joined from {new_host}:{new_port}.")
                self.broadcast_peer_list()

                if conn is not None:
                    with self.lock:
                        # Creates a list of peers to reply to existing connections with from a copy of the DHT.
                        reply_peers = self.dht.copy()

                    try:
                        # Replies back to existing connections with the updated peer list.
                        conn.send(json.dumps({"type": "peer_list", "peers": reply_peers}).encode('utf-8'))
                    except Exception as e:
                        self._output(f"[ERROR] Could not send peer list to {new_username} - {e}.")

            # Handles update control messages.
            elif control_type == "update":
                # Extracts details from a peer updating their username.
                old_username = message.get("sender")
                new_username = message.get("new_username")
                new_host = message.get("host")
                new_port = message.get("port")

                with self.lock:
                    # Finds all keys that match the host and port.
                    keys_to_update = [k for k, info in self.dht.items()
                                      if info.get("host") == new_host and info.get("port") == new_port]

                    for k in keys_to_update:
                        # Updates each matching entry.
                        self.dht[k].update({"username": new_username, "host": new_host, "port": new_port,
                                            "status": "Online"})
                        self.username_history[new_username] = k
                        self.username_history[old_username] = k

                # Logs the username change.
                self._output(f"[INFO] {old_username} changed username to {new_username}.")

            # Handles leave control messages.
            elif control_type == "leave":
                leave_peer_key = message.get("peer_key")

                with self.lock:
                    # Marks a peer as offline if they already exist online.
                    if leave_peer_key in self.dht and self.dht[leave_peer_key].get("status", "Online") != "Offline":
                        self.dht[leave_peer_key]["status"] = "Offline"
                        self._output(f"[INFO] {self.dht[leave_peer_key]['username']} has left the network.")

            # Handles requests to open a chat window.
            elif control_type == "open_chat_window":
                recipients = message.get("recipients", [])

                # Triggers a GUI event if the current user is among the recipients.
                if self.username in recipients and self.gui_event_callback:
                    self.gui_event_callback(message)

        # Processes peer list messages to update the DHT.
        elif msg_type == "peer_list":
            peers = message.get("peers", {})

            with self.lock:
                self.dht.update(peers)

        # Processes chat messages.
        elif msg_type == "chat":
            tag = message.get("tag", "[CHAT]")

            # Standard chat messages.
            if tag == "[CHAT]":
                sender = message.get("sender")
                chat_message = message.get("message")
                self._output(f"{sender}: {chat_message}")
            # Uses the GUI event callback for other chat tags.
            elif self.gui_event_callback:
                self.gui_event_callback(message)

        # Processes file transfer messages.
        elif msg_type == "file":
            if "chat_key" in message:
                print(f"DEBUG: Received file message with chat_key: {message}")

                # Triggers a GUI event for file messages in chat.
                if self.gui_event_callback:
                    self.gui_event_callback(message)

            else:
                # Extracts file information for direct file transfers.
                sender = message.get("sender")
                filename = message.get("filename")
                file_data = message.get("data")

                # Determines the Downloads directory.
                downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")

                # Creates a file path and adds a prefix to the saved file.
                filepath = os.path.join(downloads_dir, f"received_{filename}")

                # Decodes the file data from base64.
                decoded_data = base64.b64decode(file_data.encode('utf-8'))

                with open(filepath, "wb") as f:
                    # Writes the decoded file data to disk.
                    f.write(decoded_data)

                self._output(f"[FILE] Received file '{filename}' from {sender} (saved to Downloads)")

        else:
            self._output("[WARNING] Received unknown message type.")

    # Forwards a message to all connected peers (except self).
    def forward_message(self, message):
        with self.lock:
            peers = list(self.dht.items())

        for peer_key, info in peers:
            # Skips forwarding of any messages to self and non-update messages to offline peers.
            if peer_key == self.key:
                continue
            if message.get("control") != "update" and info.get("status", "Online") != "Online":
                continue

            try:
                # Creates a new socket to connect to the peer and send a message before closing the connection.
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(message).encode('utf-8'))
                s.close()
            except Exception as e:
                self._output(f"[ERROR] Could not forward message to {info.get('username', 'Unknown')} - {e}")

    # Connects to another peer given their IP and port.
    def connect_to_peer(self, peer_ip, peer_port):
        try:
            # Creates a new socket to connect to a target peer.
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((peer_ip, peer_port))

            # Prepares a join message to introduce the connecting peer.
            join_msg =  {
                        "type": "control",
                        "control": "join",
                        "sender": self.username,
                        "peer_key": self.key,
                        "host": self.host,
                        "port": self.port
                        }

            # Sends the join message and receives the updated peer list.
            s.send(json.dumps(join_msg).encode('utf-8'))
            data = s.recv(4096)

            if data:
                self._output(f"[INFO] Connected to peer at {peer_ip}:{peer_port}")

                # Loads a response from the JSON data.
                response = json.loads(data.decode('utf-8'))

                # Updates the DHT if a peer list is received.
                if response.get("type") == "peer_list":
                    with self.lock:
                        self.dht.update(response.get("peers", {}))

                    # Broadcasts the updated list.
                    self.broadcast_peer_list()
                    self._output(f"[INFO] Received updated peer list.")

            # Closes the socket when finished.
            s.close()

        except Exception as e:
            self._output(f"[ERROR] Could not connect to peer at {peer_ip}:{peer_port} - {e}")

    # Updates the username for a peer.
    def update_username(self, new_username):
        # Stores the old username for reference before updating to a new one.
        old_username = self.username
        self.username = new_username

        with self.lock:
            # Updates the DHT entry with the new username.
            if self.key in self.dht:
                self.dht[self.key].update({"username": new_username,"host": self.host,"port": self.port,
                    "status": "Online"})

            # Updates the username history.
            self.username_history[new_username] = self.key
            self.username_history[old_username] = self.key

        # Creates an update message.
        update_msg =    {
                        "type": "control",
                        "control": "update",
                        "sender": old_username,
                        "new_username": new_username,
                        "host": self.host,
                        "port": self.port
                        }

        # Forwards the update message to all peers.
        self.forward_message(update_msg)
        self._output(f"[INFO] Username updated from {old_username} to {new_username}.")

    # Retrieves a peer's information by their username.
    def get_peer_by_username(self, target_username):
        with self.lock:
            # Loops through every item of the DHT for a matching username.
            for key, info in self.dht.items():
                if info.get("username") == target_username:
                    # Returns the peer's information found by a direct match.
                    return key, info

            # Checks username history if not found directly.
            if target_username in self.username_history:
                key = self.username_history[target_username]

                if key in self.dht:
                    # Returns the peer's information found through the username history.
                    return key, self.dht[key]

        # Returns None if the peer is not found.
        return None, None

    # Generates a unique chat key for a group chat using participant identifiers.
    def get_chat_key(self, participants):
        keys = []

        for identifier in participants:
            # Uses one's own key for identification and retrieves keys to identify peers.
            if identifier == self.username:
                keys.append(self.key)
            else:
                peer_key, _ = self.get_peer_by_username(identifier)

                # Appends found keys to list if found, and falls back to identifier otherwise.
                if peer_key is not None:
                    keys.append(peer_key)
                else:
                    keys.append(identifier)

        # Removes duplicates and sorts the keys.
        keys = sorted(set(keys))

        # Joins keys to form a unique chat key.
        return ",".join(keys)

    # Reports errors for private chat actions.
    def _report_private_message(self, level, message, recipients):
        # Sends a structured message if GUI callback is available.
        if self.gui_event_callback:
            error_msg = {
                        "type": "chat",
                        "tag": "[PRIVATE]",
                        "sender": "SYSTEM",
                        "message": f"[{level.upper()}] {message}",
                        "recipients": recipients
                        }
            self.gui_event_callback(error_msg)
        # Outputs to the console otherwise.
        else:
            self._output(f"[{level.upper()}] {message}")

    # Sends a chat message to one or more participants.
    def send_chat_message(self, participants, message_text, private=False):
        # Ensures the sender is included in the recipients.
        participants = list(set(participants + [self.username]))
        valid_recipients = []

        # Determines if the chat is private (only two participants).
        is_private_chat = (len(participants) == 2)

        for r in participants:
            peer_key, info = self.get_peer_by_username(r)

            # Validates whether the recipients are online.
            if info and info.get("status", "Online") == "Online":
                valid_recipients.append((peer_key, info))
            else:
                if is_private_chat:
                    self._report_private_message("warning", f"User {r} is offline or not found.", participants)

        # Prepares the chat message based on privacy and determines the appropriate tag.
        if private:
            chat_key = self.get_chat_key(participants)
            tag = "[PRIVATE]" if is_private_chat else "[GROUP]"

            chat_msg =  {
                        "type": "chat",
                        "sender": self.username,
                        "message": message_text,
                        "tag": tag,
                        "recipients": participants,
                        "chat_key": chat_key
                        }

        else:
            chat_msg =  {
                        "type": "chat",
                        "sender": self.username,
                        "message": message_text,
                        "tag": "[CHAT]"
                        }

        # Sends the message to each valid recipient.
        for peer_key, info in valid_recipients:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(chat_msg).encode('utf-8'))
                s.close()
            except Exception as e:
                if is_private_chat:
                    self._report_private_message("error", f"Could not send message to {info['username']} - {e}",
                                                 participants)

    # Broadcasts a public chat message to all online peers.
    def broadcast_chat_message(self, message_text):
        # Prepares a standard chat message.
        chat_msg =  {
                    "type": "chat",
                    "sender": self.username,
                    "message": message_text,
                    "tag": "[CHAT]"
                    }

        with self.lock:
            peers = list(self.dht.items())

        # Sends the message to every peer except self and offline peers.
        for peer_key, info in peers:
            if peer_key == self.key or info.get("status", "Online") != "Online":
                continue

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(chat_msg).encode('utf-8'))
                s.close()
            except Exception as e:
                self._output(f"[ERROR] Could not send message to {info.get('username', 'Unknown')} - {e}")

    # Sends a file to a specific peer.
    def send_file(self, target_username, file_path, private=False, chat_key=None, recipients=None):
        # Checks if the file exists.
        if not os.path.exists(file_path):
            self._output(f"[ERROR] File '{file_path}' does not exist.")
            return

        # Gets the absolute path of the file.
        file_path = os.path.abspath(file_path)
        print(f"DEBUG: Sending file from: {file_path} (exists: {os.path.exists(file_path)})")

        # Retrieves the target peer's details.
        peer_key, info = self.get_peer_by_username(target_username)

        if info:
            # Warns the user if the target peer is offline.
            if info.get("status", "Online") != "Online":
                self._output(f"[WARNING] User {target_username} is offline; file not sent.")
                return

            try:
                with open(file_path, "rb") as f:
                    # Reads the file as bytes.
                    file_bytes = f.read()

                # Encodes the file data in base64.
                file_data = base64.b64encode(file_bytes).decode('utf-8')

                # Prepares the file message.
                file_msg =  {
                            "type": "file",
                            "sender": self.username,
                            "filename": os.path.basename(file_path),
                            "data": file_data
                            }

                # Adds additional tags for private transfers.
                if private and chat_key:
                    file_msg["tag"] = "[FILE]"
                    file_msg["chat_key"] = chat_key

                    if recipients is not None:
                        file_msg["recipients"] = recipients

                else:
                    file_msg["tag"] = "[FILE]"

                # Sends the file message.
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(file_msg).encode('utf-8'))
                s.close()

                self._output(f"[FILE] File '{file_path}' sent to {target_username}")

            except Exception as e:
                self._output(f"[ERROR] Could not send file to {target_username} - {e}")

        else:
            self._output(f"[WARNING] User {target_username} not found in network.")

    # Broadcasts a file to all online peers.
    def broadcast_file(self, file_path):
        # Checks if the file exists.
        if not os.path.exists(file_path):
            self._output(f"[ERROR] File '{file_path}' does not exist.")
            return

        try:
            with open(file_path, "rb") as f:
                # Reads the file as bytes.
                file_bytes = f.read()

            # Encodes the file in base64.
            file_data = base64.b64encode(file_bytes).decode('utf-8')

            # Prepares the broadcast file message.
            file_msg =  {
                        "type": "file",
                        "sender": self.username,
                        "filename": os.path.basename(file_path),
                        "data": file_data,
                        "tag": "[FILE]"
                        }

            with self.lock:
                peers = list(self.dht.items())

            # Sends the file to each online peer except self.
            for peer_key, info in peers:
                if peer_key == self.key or info.get("status", "Online") != "Online":
                    continue

                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect((info["host"], info["port"]))
                    s.send(json.dumps(file_msg).encode('utf-8'))
                    s.close()
                except Exception as e:
                    self._output(f"[ERROR] Could not broadcast file to {info.get('username', 'Unknown')} - {e}")

        except Exception as e:
            self._output(f"[ERROR] Could not read file '{file_path}' - {e}")

    # Disconnects a peer from the network, notifying all other peers.
    def disconnect(self):
        with self.lock:
            peers = list(self.dht.items())

        # Loops through each peer to send a leave message through a socket.
        for peer_key, info in peers:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))

                leave_msg = {
                            "type": "control",
                            "control": "leave",
                            "sender": self.username,
                            "peer_key": self.key
                            }

                s.send(json.dumps(leave_msg).encode('utf-8'))
                s.close()

            except Exception as e:
                self._output(f"[ERROR] Could not send disconnect message to {info.get('username', 'Unknown')} - {e}")

        # Closes the server socket.
        self.server_socket.close()

    # Notifies peers to open a chat window for a given set of participants.
    def notify_open_chat_window(self, participants):
        # Ensures the current user is included.
        participants = list(set(participants + [self.username]))

        # Generates a unique chat key.
        chat_key = self.get_chat_key(participants)

        # Loops through recipients except self.
        for r in participants:
            if r == self.username:
                continue

            peer_key, info = self.get_peer_by_username(r)

            if info:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect((info["host"], info["port"]))

                    msg =   {
                            "type": "control",
                            "control": "open_chat_window",
                            "sender": self.username,
                            "recipients": participants,
                            "chat_key": chat_key
                            }

                    s.send(json.dumps(msg).encode('utf-8'))
                    s.close()

                except Exception as e:
                    self._output(f"[ERROR] Could not notify {info['username']} to open chat window - {e}")

    # Outputs messages either to the GUI (if available) or to the console.
    def _output(self, message):
        if self.gui_callback:
            self.gui_callback(message)
        else:
            print(message)


# Defines the LoginWindow class using tkinter for the initial login screen.
class LoginWindow(tk.Tk):
    # Initializes the login window.
    def __init__(self):
        super().__init__()
        # Sets the window title.
        self.title("Login")

        # Adds a label for the username, a variable to hold the input, and an entry widget.
        tk.Label(self, text="Username:").pack(padx=10, pady=5)
        self.username_var = tk.StringVar()
        tk.Entry(self, textvariable=self.username_var).pack(padx=10, pady=5)

        # Adds a label for the port, a variable to hold the input, and an entry widget.
        tk.Label(self, text="Port:").pack(padx=10, pady=5)
        self.port_var = tk.StringVar()
        tk.Entry(self, textvariable=self.port_var).pack(padx=10, pady=5)

        # Adds a login button that triggers the on_login method.
        tk.Button(self, text="Login", command=self.on_login).pack(padx=10, pady=10)

    # Handles the login event.
    def on_login(self):
        # Gets and strips the username and port input.
        username = self.username_var.get().strip()
        port = self.port_var.get().strip()

        # Ensures both fields are filled.
        if not username or not port:
            messagebox.showerror("Error", "Username and port are required")
            return

        # Ensures the port number is an integer.
        try:
            port = int(port)
        except ValueError:
            messagebox.showerror("Error", "Port must be an integer")
            return

        # Closes the login window.
        self.destroy()

        # Opens the main GUI window.
        main_gui = MainGUI(username, port)
        main_gui.run()


# Defines a class for the private chat tab within the GUI.
class PrivateChatTab(tk.Frame):
    # Initializes the private chat tab.
    def __init__(self, parent, peer, participants, chat_key):
        super().__init__(parent)

        # Stores the peer object, list of chat participants, and the unique chat key.
        self.peer = peer
        self.participants = participants
        self.chat_key = chat_key

        # Creates a scrolled text widget for the chat log.
        self.chat_log = scrolledtext.ScrolledText(self, state='disabled', wrap='word')
        self.chat_log.pack(fill=tk.BOTH, expand=True)

        # Configures text colors for different message types.
        self.chat_log.tag_configure("own", foreground="green")
        self.chat_log.tag_configure("peer", foreground="blue")
        self.chat_log.tag_configure("system", foreground="black")

        # Creates a frame for the message entry area.
        self.entry_frame = tk.Frame(self)
        self.entry_frame.pack(fill=tk.X)

        # Adds an entry widget for typing messages.
        self.entry = tk.Entry(self.entry_frame)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)

        # Binds the Return key to send messages.
        self.entry.bind("<Return>", self.send_message)

        # Adds a send button to trigger message sending.
        self.send_button = tk.Button(self.entry_frame, text="Send", command=self.send_message)
        self.send_button.pack(side=tk.LEFT, padx=5)

        # Adds a file button to allow uploading files.
        self.file_button = tk.Button(self.entry_frame, text="+", command=self.upload_file)
        self.file_button.pack(side=tk.LEFT, padx=5)

    # Appends a new line to the chat log with a specific tag.
    def append_chat_log(self, text, tag):
        self.chat_log.configure(state='normal')
        self.chat_log.insert(tk.END, text + "\n", tag)
        self.chat_log.configure(state='disabled')
        self.chat_log.see(tk.END)

    # Handles sending a message from the chat entry.
    def send_message(self, event=None):
        msg = self.entry.get().strip()

        if msg:
            # Sends the chat message as a private message and appends it to the chat log.
            self.peer.send_chat_message(self.participants, msg, private=True)
            self.append_chat_log(f"You: {msg}", "own")

            # Clears the entry widget.
            self.entry.delete(0, tk.END)

    # Handles uploading and sending a file.
    def upload_file(self):
        # Sets the initial directory to the Downloads folder.
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")

        # Opens a file dialog for selecting a file.
        filepath = filedialog.askopenfilename(initialdir=downloads_dir)

        if filepath:
            for recipient in self.participants:
                if recipient != self.peer.username:
                    # Sends the file to all recipients except self.
                    self.peer.send_file(recipient, filepath,
                                        private=True, chat_key=self.chat_key, recipients=self.participants)

            self.append_chat_log(f"[FILE] File sent: {os.path.basename(filepath)}", "system")


# Defines the main GUI class for the peer-to-peer chat application.
class MainGUI:
    # Initializes the main GUI.
    def __init__(self, username, port):
        # Stores the username and port.
        self.username = username
        self.port = port

        # Creates a Peer instance.
        self.peer = Peer(username, host='127.0.0.1', port=port)

        # Starts the peer server to listen for connections, and a queue for incoming messages.
        self.peer.start_server()
        self.msg_queue = queue.Queue()

        # Sets up GUI callbacks in the Peer instance.
        self.peer.gui_callback = self.enqueue_message
        self.peer.gui_event_callback = self.handle_peer_event

        # Dictionary to manage private chat tabs.
        self.private_chats = {}

        # Creates the root tkinter window, setting the window title with connection info.
        self.root = tk.Tk()
        self.root.title(f"Peer Chat - {username}@127.0.0.1:{port}")

        # Creates a left frame for controls.
        self.left_frame = tk.Frame(self.root, width=200)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y)

        # Creates a button showing the current username.
        self.username_button = tk.Button(self.left_frame, text=username, command=self.open_username_menu)
        self.username_button.pack(pady=5)

        # Creates a button to open the connect window.
        self.connect_button = tk.Button(self.left_frame, text="Connect", command=self.open_connect_window)
        self.connect_button.pack(pady=5)

        # Displays a list of connected peers.
        tk.Label(self.left_frame, text="Peers:").pack(pady=5)
        self.peers_list_frame = tk.Frame(self.left_frame)
        self.peers_list_frame.pack(fill=tk.BOTH, expand=True)

        # Button to open a message window.
        self.message_button = tk.Button(self.left_frame, text="Message", command=self.open_message_window)
        self.message_button.pack(side=tk.BOTTOM, pady=5)

        # Creates a frame for public chat messages.
        self.public_frame = tk.Frame(self.root)
        self.public_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Creates a scrolled text widget for the public chat log.
        self.chat_log = scrolledtext.ScrolledText(self.public_frame, state='disabled', wrap='word')
        self.chat_log.pack(fill=tk.BOTH, expand=True)

        # Configures tag colors for different message types.
        self.chat_log.tag_configure("own", foreground="green")
        self.chat_log.tag_configure("peer", foreground="blue")
        self.chat_log.tag_configure("system", foreground="black")

        # Displays initial connection information and available commands.
        initial_info = f"Working Directory: {os.getcwd()}\nAddress: 127.0.0.1:{self.port}\nCommands:\n" \
                       "/connect <ip> <port>\n/update <new_username>\n/msg <user1,user2,...> <message>\n" \
                       "/sendfile <user1,user2,...> <filepath>\n/exit\n"

        self.append_chat_log(initial_info, "system")

        # Creates a frame for command input.
        self.entry_frame = tk.Frame(self.public_frame)
        self.entry_frame.pack(fill=tk.X)

        # Creates an entry widget for commands.
        self.command_entry = tk.Entry(self.entry_frame)
        self.command_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)

        # Binds the Return key to process commands.
        self.command_entry.bind("<Return>", self.process_command)

        # Creates a send button for commands.
        self.send_button = tk.Button(self.entry_frame, text="Send", command=self.process_command)
        self.send_button.pack(side=tk.LEFT, padx=5)

        # Creates a file button to allow file uploads in public chat.
        self.file_button = tk.Button(self.entry_frame, text="+", command=self.upload_file_public)
        self.file_button.pack(side=tk.LEFT, padx=5)

        # Creates a tabbed chat interface for private/group chats.
        self.chat_tabs = ttk.Notebook(self.root)
        self.chat_tabs.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    # Generates a chat key based on chat participants.
    def get_chat_key(self, participants):
        participants = list(set(participants + [self.peer.username]))
        return self.peer.get_chat_key(participants)

    # Enqueues messages received from the peer object.
    def enqueue_message(self, message):
        self.msg_queue.put(message)

    # Processes messages in the queue and update sthe public chat log.
    def process_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                tag = "peer"

                # Determines the tag based on message type.
                if (msg.startswith("[INFO]") or msg.startswith("[WARNING]")
                        or msg.startswith("[ERROR]") or msg.startswith("[FILE]")):
                    tag = "system"
                if msg.startswith("[CHAT]"):
                    msg = msg.replace("[CHAT] ", "", 1)

                # Skips tags for private chat and group chat windows.
                if msg.startswith("[PRIVATE]") or msg.startswith("[GROUP]"):
                    continue

                # Appends the message to the public chat log.
                self.append_chat_log(msg, tag)

        except queue.Empty:
            pass

        # Schedules the next queue check.
        self.root.after(100, self.process_queue)

    # Appends text to the public chat log.
    def append_chat_log(self, text, tag=None):
        self.chat_log.configure(state='normal')
        self.chat_log.insert(tk.END, text + "\n", tag)
        self.chat_log.configure(state='disabled')
        self.chat_log.see(tk.END)

    # Processes commands entered by the user.
    def process_command(self, event=None):
        command = self.command_entry.get().strip()

        if command:
            # Treats commands starting with a forward slash as a special command.
            if command.startswith("/"):
                # Reads the first word of text entry as the type of command.
                parts = command.split(" ", 1)
                cmd = parts[0]

                # Handles the connect command.
                if cmd == "/connect":
                    if len(parts) < 2:
                        self.append_chat_log("[WARNING] Usage: /connect <ip> <port>", "system")
                    else:
                        # Identifies the command arguments.
                        args = parts[1].split()

                        if len(args) == 2:
                            # Identifies the IP address.
                            ip = args[0]

                            try:
                            # Identifies the port number.
                                port = int(args[1])
                                self.peer.connect_to_peer(ip, port)
                            except ValueError:
                                self.append_chat_log("[ERROR] Port must be an integer.", "system")

                        else:
                            self.append_chat_log("[WARNING] Usage: /connect <ip> <port>", "system")

                # Handles the update username command.
                elif cmd == "/update":
                    if len(parts) < 2:
                        self.append_chat_log("[WARNING] Usage: /update <new_username>", "system")
                    else:
                        # Identifies the new username.
                        new_username = parts[1].strip()
                        self.peer.update_username(new_username)
                        self.username_button.config(text=new_username)

                # Handles the message command.
                elif cmd == "/msg":
                    if len(parts) < 2 or " " not in parts[1]:
                        self.append_chat_log("[WARNING] Usage: /msg <user1,user2,...> <message>", "system")
                    else:
                        # Distinguishes recipients from each other by commas.
                        recipients_str, message_text = parts[1].split(" ", 1)
                        recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]

                        if recipients:
                            # Creates a chat window.
                            self.open_chat_tab(recipients)
                            chat_key = self.get_chat_key(recipients)
                            chat_tab = self.private_chats[chat_key]

                            # Distinguishes the user's messages from that of their peers'.
                            chat_tab.append_chat_log(f"You: {message_text}", "own")
                            self.peer.send_chat_message(recipients, message_text, private=True)

                        else:
                            self.append_chat_log("[WARNING] No valid recipients provided.", "system")

                # Handles the send file command.
                elif cmd == "/sendfile":
                    if len(parts) < 2 or " " not in parts[1]:
                        self.append_chat_log("[WARNING] Usage: /sendfile <user1,user2,...> <filepath>",
                                             "system")
                    else:
                        # Distinguishes recipients from each other by commas.
                        recipients_str, filepath = parts[1].split(" ", 1)
                        recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]

                        if recipients:
                            # Adds self to the recipients and creates a chat key.
                            recipients = list(set(recipients + [self.peer.username]))
                            chat_key = self.get_chat_key(recipients)

                            for recipient in recipients:
                                if recipient != self.peer.username:
                                    # Sends a file to every recipient except for self.
                                    self.peer.send_file(recipient, filepath.strip(),
                                                        private=True, chat_key=chat_key, recipients=recipients)

                        else:
                            self.append_chat_log("[WARNING] No valid recipients provided.", "system")

                # Handles the exit command.
                elif cmd == "/exit":
                    self.peer.disconnect()
                    self.root.destroy()
                    sys.exit(0)

                # Handles unknown commands.
                else:
                    self.append_chat_log("[WARNING] Unknown command.", "system")

            else:
                # Broadcasts text entries as public messages if no command prefix is given.
                self.peer.broadcast_chat_message(command)
                self.append_chat_log(f"You: {command}", "own")

            # Clears the command entry after processing.
            self.command_entry.delete(0, tk.END)

    # Allows a file to be publicly uploaded.
    def upload_file_public(self):
        filed = filedialog.askopenfilename()

        if filed:
            self.peer.broadcast_file(filed)
            self.append_chat_log(f"[FILE] Public file sent: {os.path.basename(filed)}", "system")

    # Updates the list of peers shown in the GUI.
    def update_peers_list(self):
        # Removes all existing peer buttons.
        for widget in self.peers_list_frame.winfo_children():
            widget.destroy()

        with self.peer.lock:
            # Loops through each peer of the updated list.
            for key, info in self.peer.dht.items():
                # Skips self.
                if key == self.peer.key:
                    continue

                # Recreates buttons from peer info to form the new list display.
                username = info.get("username", "Unknown")
                status = info.get("status", "Offline")
                color = "green" if status == "Online" else "red"
                btn = tk.Button(self.peers_list_frame, text=username, fg=color,
                                command=lambda u=username: self.open_chat_tab(u))
                btn.pack(fill=tk.X, padx=5, pady=2)

        # Schedules the next update of the peers list.
        self.root.after(5000, self.update_peers_list)

    # Opens a window to connect to a new peer.
    def open_connect_window(self):
        # Creates the window display.
        win = tk.Toplevel(self.root)
        win.title("Connect to Peer")
        tk.Label(win, text="Peer IP:Port").pack(pady=5)
        entry = tk.Entry(win)
        entry.pack(pady=5)

        # Function to process the connection input.
        def connect():
            data = entry.get().strip()

            # Ensures IP and port number entry follow a specified format.
            if ":" in data:
                # Distinguishes the IP and port number from each other.
                ip, port_str = data.split(":", 1)

                try:
                    # Ensures the port is an integer before using it to connect to a peer.
                    port = int(port_str)
                    self.peer.connect_to_peer(ip.strip(), port)

                    # Closes the window when text is entered.
                    win.destroy()

                except ValueError:
                    messagebox.showerror("Error", "Port must be an integer")

            else:
                messagebox.showerror("Error", "Input must be in the format IP:Port")

        # Creates the button to confirm connection attempt.
        tk.Button(win, text="Connect", command=connect).pack(pady=10)

    # Opens a menu to change the username or logout.
    def open_username_menu(self):
        # Creates the menu display.
        win = tk.Toplevel(self.root)
        win.title("Username Options")
        tk.Label(win, text=f"Current Username: {self.peer.username}").pack(pady=5)

        # Function to change username.
        def change_username():
            win.destroy()
            self.open_change_username_window()

        # Function to log out.
        def logout():
            self.peer.disconnect()
            self.root.destroy()
            sys.exit(0)

        # List of menu buttons to appear.
        tk.Button(win, text="Change Username", command=change_username).pack(pady=5)
        tk.Button(win, text="Logout", command=logout).pack(pady=5)
        tk.Button(win, text="Cancel", command=win.destroy).pack(pady=5)

    # Opens a window to change the username.
    def open_change_username_window(self):
        # Creates the window display.
        win = tk.Toplevel(self.root)
        win.title("Change Username")
        tk.Label(win, text="New Username:").pack(pady=5)
        username_entry = tk.Entry(win)
        username_entry.pack(pady=5)

        # Function to process the username change.
        def change():
            new_username = username_entry.get().strip()

            if new_username:
                self.peer.update_username(new_username)
                self.username_button.config(text=new_username)
                win.destroy()
            else:
                messagebox.showerror("Error", "Username cannot be empty")

        # Creates the button to confirm the change.
        tk.Button(win, text="Change", command=change).pack(pady=10)

    # Opens a window to select peers for messaging.
    def open_message_window(self):
        # Creates the window display.
        win = tk.Toplevel(self.root)
        win.title("Select Peers for Message")

        # The peers that the user selects to message.
        selections = {}

        with self.peer.lock:
            for key, info in self.peer.dht.items():
                # Skips self.
                if key == self.peer.key:
                    continue

                # Creates a checkbox for each peer.
                var = tk.BooleanVar()
                selections[info["username"]] = var
                tk.Checkbutton(win, text=info["username"], variable=var).pack(anchor='w')

        # Confirms the selection and opens a group chat.
        def confirm():
            selected = [username for username, i in selections.items() if i.get()]

            if selected:
                # Selects list of users, closes the previous window, and opens a new chat tab.
                selected = list(set(selected + [self.peer.username]))
                win.destroy()
                self.open_chat_tab(selected)
            else:
                messagebox.showerror("Error", "No peers selected")

        # Creates the confirm button.
        tk.Button(win, text="Confirm", command=confirm).pack(pady=10)

    # Opens or creates a group chat tab.
    def open_chat_tab(self, participants):
        # If participants is not a list (e.g. it's a string), wrap it in a list.
        if not isinstance(participants, list):
            participants = [participants]

        # Determines a chat key from a list of participants that includes self.
        participants = list(set(participants + [self.peer.username]))
        chat_key = self.get_chat_key(participants)

        if chat_key in self.private_chats:
            # Selects an existing chat tab.
            self.chat_tabs.select(self.private_chats[chat_key])
        else:
            # Creates a new chat tab.
            chat_tab = PrivateChatTab(self.chat_tabs, self.peer, participants, chat_key)
            self.private_chats[chat_key] = chat_tab

            # Creates a tab title from the participant names (excluding self).
            other_names = sorted([name for name in participants if name != self.peer.username])
            tab_title = ", ".join(other_names)
            self.chat_tabs.add(chat_tab, text=tab_title)

            # Notifies other peers to open the chat window and makes it the active tab.
            self.peer.notify_open_chat_window(participants)
            self.chat_tabs.select(chat_tab)

    # Handles events received from peers (chat windows and file messages).
    def handle_peer_event(self, message):
        if message.get("type") == "control" and message.get("control") == "open_chat_window":
            # Generates chat key from participants.
            participants = message.get("recipients", [])
            chat_key = message.get("chat_key", self.get_chat_key(participants))

            # Stops duplicate windows from forming.
            if chat_key not in self.private_chats:
                self.open_chat_tab(participants)
            else:
                self.chat_tabs.select(self.private_chats[chat_key])

        # Handles chat type messages.
        elif message.get("type") == "chat":
            # Extracts tag from message.
            tag = message.get("tag", "[CHAT]")

            if tag in ("[PRIVATE]", "[GROUP]"):
                # Extracts information from the message.
                chat_key = message.get("chat_key")
                sender = message.get("sender")
                msg_text = message.get("message")

                if chat_key in self.private_chats:
                    # Locates a chat tab by the chat key.
                    chat_tab = self.private_chats[chat_key]

                    # Prevents messages sent by self from being redundantly added twice to the window.
                    if sender != self.peer.username:
                        chat_tab.append_chat_log(f"{sender}: {msg_text}", "peer")

                else:
                    # Opens a chat tab with the participants.
                    participants = message.get("recipients", [])
                    self.open_chat_tab(participants)

                    # Generates chat key from participants.
                    chat_key = self.get_chat_key(participants)

                    if chat_key in self.private_chats:
                        # Locates a chat tab by the chat key.
                        chat_tab = self.private_chats[chat_key]

                        # Prevents messages sent by self from being redundantly added twice to the window.
                        if sender != self.peer.username:
                            chat_tab.append_chat_log(f"{sender}: {msg_text}", "peer")

        # Handles file type messages.
        elif message.get("type") == "file":
            if "chat_key" in message:
                # Extracts information from the message.
                chat_key = message.get("chat_key")
                sender = message.get("sender")
                filename = message.get("filename")

                if chat_key in self.private_chats:
                    # Locates a chat tab by the chat key and appends file message to that tab.
                    chat_tab = self.private_chats[chat_key]
                    chat_tab.append_chat_log(f"[FILE] Received '{filename}' from {sender}", "peer")
                else:
                    recipients = message.get("recipients", [])

                    if recipients:
                        # Opens a chat tab with the participants.
                        self.open_chat_tab(recipients)

                        # Generates chat key from participants.
                        chat_key = self.get_chat_key(recipients)

                        if chat_key in self.private_chats:
                            # Locates a chat tab by the chat key and appends file message to that tab.
                            chat_tab = self.private_chats[chat_key]
                            chat_tab.append_chat_log(f"[FILE] Received '{filename}' from {sender}", "peer")

                    else:
                        self.append_chat_log(f"[FILE] Received '{filename}' from {sender}", "system")

    # Starts the GUI main loop and sets up periodic tasks.
    def run(self):
        self.root.after(100, self.process_queue)
        self.root.after(5000, self.update_peers_list)
        self.root.mainloop()


# Defines the main entry point of the application.
def main():
    # Creates the login window and starts its event loop.
    login = LoginWindow()
    login.mainloop()


# Runs the main function.
if __name__ == "__main__":
    main()
