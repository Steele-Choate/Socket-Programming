# Imported packages
import socket
import threading
import json
import base64
import os
import sys
import hashlib
import queue
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk


# Define the main Peer class that represents each node in the P2P network.
class Peer:
    # Initialize a new peer with a username, host, and port.
    def __init__(self, username, host='127.0.0.1', port=5000):
        # Store the username.
        self.username = username
        # Store the host address (default is localhost).
        self.host = host
        # Store the port number.
        self.port = port

        # Generate a unique key for this peer based on host and port.
        self.key = str(self.hash_identity(self.host, self.port))
        # Keep a history mapping usernames to their unique keys.
        self.username_history = {username: self.key}
        # Callback for GUI output (if one is provided).
        self.gui_callback = None
        # Callback for GUI events (e.g. updating chat windows).
        self.gui_event_callback = None

        # Create a server socket for incoming connections.
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Set socket option to allow reusing the address.
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind the socket to the specified host and port.
        self.server_socket.bind((self.host, self.port))
        # Listen for incoming connections; allow up to 5 pending connections.
        self.server_socket.listen(5)

        # Create a distributed hash table (DHT) to store peer information.
        self.dht = {
            self.key: {
                "username": self.username,
                "host": self.host,
                "port": self.port,
                "status": "Online"
            }
        }
        # Create a lock for thread-safe updates to shared data.
        self.lock = threading.Lock()

    # Static method to generate a hash identity from host and port.
    @staticmethod
    def hash_identity(host, port):
        # Create a string representation of the identity.
        identity_str = f"{host}:{port}"
        # Generate a SHA1 hash and convert it to an integer.
        return int(hashlib.sha1(identity_str.encode()).hexdigest(), 16)

    # Start the peer's server in a separate thread.
    def start_server(self):
        threading.Thread(target=self.listen_for_connections, daemon=True).start()

    # Listen for incoming socket connections.
    def listen_for_connections(self):
        # Output listening information.
        self._output(f"[INFO] Listening on {self.host}:{self.port}.")
        while True:
            try:
                # Accept a new connection.
                conn, addr = self.server_socket.accept()
                # Handle the connection in a new thread.
                threading.Thread(target=self.handle_connection, args=(conn, addr), daemon=True).start()
            except Exception as e:
                # Report errors if a connection cannot be accepted.
                self._output(f"[ERROR] Error accepting connections: {e}")

    # Handle an individual connection from another peer.
    def handle_connection(self, conn, addr):
        while True:
            # Receive data (up to 4096 bytes).
            data = conn.recv(4096)
            # If no data is received, break the loop.
            if not data:
                break
            # Decode the data from bytes to a string and load the JSON message.
            message = json.loads(data.decode('utf-8'))
            # Process the received message.
            self.process_message(message, conn)
        # Close the connection when done.
        conn.close()

    # Broadcast the updated peer list to all connected peers.
    def broadcast_peer_list(self):
        # Lock access to shared resources.
        with self.lock:
            # Prepare a message with the current DHT (peer list).
            peer_list_message = {"type": "peer_list", "peers": self.dht}
            # Loop through each peer in the DHT.
            for peer_key, info in list(self.dht.items()):
                # Skip sending to self.
                if peer_key == self.key:
                    continue
                try:
                    # Create a new socket for the peer.
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    # Connect to the peer's host and port.
                    s.connect((info["host"], info["port"]))
                    # Send the peer list message.
                    s.send(json.dumps(peer_list_message).encode('utf-8'))
                    # Close the socket.
                    s.close()
                except Exception as e:
                    # Report errors if unable to send the update.
                    self._output(f"[ERROR] Could not send updated peer list to {info.get('username', 'Unknown')} - {e}")

    # Process a received message based on its type.
    def process_message(self, message, conn=None):
        # Get the message type.
        msg_type = message.get("type")
        # Process control messages (join, update, leave, open_chat_window).
        if msg_type == "control":
            # Get the specific control type.
            control_type = message.get("control")
            if control_type == "join":
                # Extract details for a new peer joining.
                new_username = message.get("sender")
                new_peer_key = str(message.get("peer_key"))
                new_host = message.get("host")
                new_port = message.get("port")
                # Safely update the DHT with the new peer.
                with self.lock:
                    self.dht[new_peer_key] = {
                        "username": new_username,
                        "host": new_host,
                        "port": new_port,
                        "status": "Online"
                    }
                    self.username_history[new_username] = new_peer_key
                # Log the new join event.
                self._output(f"[INFO] {new_username} joined from {new_host}:{new_port}.")
                # Broadcast the updated peer list.
                self.broadcast_peer_list()
                # If the connection exists, reply with the updated peer list.
                if conn is not None:
                    with self.lock:
                        reply_peers = self.dht.copy()
                    try:
                        conn.send(json.dumps({"type": "peer_list", "peers": reply_peers}).encode('utf-8'))
                    except Exception as e:
                        self._output(f"[ERROR] Could not send peer list to {new_username} - {e}.")
            # Handle username update control messages.
            elif control_type == "update":
                old_username = message.get("sender")
                new_username = message.get("new_username")
                new_host = message.get("host")
                new_port = message.get("port")
                with self.lock:
                    # Find all keys that match the host and port.
                    keys_to_update = [k for k, info in self.dht.items() if info.get("host") == new_host and info.get("port") == new_port]
                    # Update each matching entry.
                    for k in keys_to_update:
                        self.dht[k].update({
                            "username": new_username,
                            "host": new_host,
                            "port": new_port,
                            "status": "Online"
                        })
                        self.username_history[new_username] = k
                        self.username_history[old_username] = k
                # Log the username change.
                self._output(f"[INFO] {old_username} changed username to {new_username}.")
            # Handle leave control messages.
            elif control_type == "leave":
                leave_peer_key = message.get("peer_key")
                with self.lock:
                    # If the peer exists and is online, mark them as offline.
                    if leave_peer_key in self.dht and self.dht[leave_peer_key].get("status", "Online") != "Offline":
                        self.dht[leave_peer_key]["status"] = "Offline"
                        self._output(f"[INFO] {self.dht[leave_peer_key]['username']} has left the network.")
            # Handle requests to open a chat window.
            elif control_type == "open_chat_window":
                recipients = message.get("recipients", [])
                # If the current user is among the recipients, trigger a GUI event.
                if self.username in recipients and self.gui_event_callback:
                    self.gui_event_callback(message)
        # Process peer list messages to update the DHT.
        elif msg_type == "peer_list":
            peers = message.get("peers", {})
            with self.lock:
                self.dht.update(peers)
        # Process chat messages.
        elif msg_type == "chat":
            tag = message.get("tag", "[CHAT]")
            # Standard chat messages.
            if tag == "[CHAT]":
                sender = message.get("sender")
                chat_message = message.get("message")
                self._output(f"{sender}: {chat_message}")
            else:
                # For other chat tags (e.g. private or group), use the GUI event callback.
                if self.gui_event_callback:
                    self.gui_event_callback(message)
        # Process file transfer messages.
        elif msg_type == "file":
            if "chat_key" in message:
                # For file messages in chat, trigger a GUI event.
                print(f"DEBUG: Received file message with chat_key: {message}")
                if self.gui_event_callback:
                    self.gui_event_callback(message)
            else:
                # For direct file transfers, extract file information.
                sender = message.get("sender")
                filename = message.get("filename")
                file_data = message.get("data")
                # Determine the Downloads directory.
                downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                # Create a file path with a prefix.
                filepath = os.path.join(downloads_dir, f"received_{filename}")
                # Decode the file data from base64.
                decoded_data = base64.b64decode(file_data.encode('utf-8'))
                # Write the decoded file data to disk.
                with open(filepath, "wb") as f:
                    f.write(decoded_data)
                # Inform the user that the file was received.
                self._output(f"[FILE] Received file '{filename}' from {sender} (saved to Downloads)")
        else:
            # Handle unknown message types.
            self._output("[WARNING] Received unknown message type.")

    # Forward a message to all connected peers (except self).
    def forward_message(self, message):
        with self.lock:
            peers = list(self.dht.items())
        for peer_key, info in peers:
            # Skip self.
            if peer_key == self.key:
                continue
            # Skip forwarding if peer is offline (except for update messages).
            if message.get("control") != "update" and info.get("status", "Online") != "Online":
                continue
            try:
                # Create a new socket and connect to the peer.
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                # Send the message.
                s.send(json.dumps(message).encode('utf-8'))
                # Close the connection.
                s.close()
            except Exception as e:
                # Report any errors encountered.
                self._output(f"[ERROR] Could not forward message to {info.get('username', 'Unknown')} - {e}")

    # Connect to another peer given its IP and port.
    def connect_to_peer(self, peer_ip, peer_port):
        try:
            # Create a new socket for the connection.
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Connect to the target peer.
            s.connect((peer_ip, peer_port))
            # Prepare a join message to introduce ourselves.
            join_msg = {
                "type": "control",
                "control": "join",
                "sender": self.username,
                "peer_key": self.key,
                "host": self.host,
                "port": self.port
            }
            # Send the join message.
            s.send(json.dumps(join_msg).encode('utf-8'))
            # Receive the updated peer list.
            data = s.recv(4096)
            if data:
                response = json.loads(data.decode('utf-8'))
                # Update our DHT if a peer list is received.
                if response.get("type") == "peer_list":
                    with self.lock:
                        self.dht.update(response.get("peers", {}))
                    # Broadcast our updated list.
                    self.broadcast_peer_list()
                    self._output(f"[INFO] Received updated peer list.")
                self._output(f"[INFO] Connected to peer at {peer_ip}:{peer_port}")
            # Close the socket.
            s.close()
        except Exception as e:
            # Report connection errors.
            self._output(f"[ERROR] Could not connect to peer at {peer_ip}:{peer_port} - {e}")

    # Update the username for this peer.
    def update_username(self, new_username):
        # Store the old username for reference.
        old_username = self.username
        # Update the username.
        self.username = new_username
        with self.lock:
            # Update the DHT entry with the new username.
            if self.key in self.dht:
                self.dht[self.key].update({
                    "username": new_username,
                    "host": self.host,
                    "port": self.port,
                    "status": "Online"
                })
            # Update username history.
            self.username_history[new_username] = self.key
            self.username_history[old_username] = self.key
        # Create an update message.
        update_msg = {
            "type": "control",
            "control": "update",
            "sender": old_username,
            "new_username": new_username,
            "host": self.host,
            "port": self.port
        }
        # Forward the update message to all peers.
        self.forward_message(update_msg)
        # Output the username change.
        self._output(f"[INFO] Username updated from {old_username} to {new_username}.")

    # Retrieve a peer's information by their username.
    def get_peer_by_username(self, target_username):
        with self.lock:
            for key, info in self.dht.items():
                if info.get("username") == target_username:
                    return key, info
            # Check username history if not found directly.
            if target_username in self.username_history:
                key = self.username_history[target_username]
                if key in self.dht:
                    return key, self.dht[key]
        # Return None if the peer is not found.
        return None, None

    # Generate a unique chat key for a group chat using participant identifiers.
    def get_chat_key(self, participants):
        keys = []
        for identifier in participants:
            # If the participant is self, use our key.
            if identifier == self.username:
                keys.append(self.key)
            else:
                # Otherwise, retrieve the peer's key.
                peer_key, _ = self.get_peer_by_username(identifier)
                if peer_key is not None:
                    keys.append(peer_key)
                else:
                    # Fallback to identifier if not found.
                    keys.append(identifier)
        # Remove duplicates and sort the keys.
        keys = sorted(set(keys))
        # Join keys to form a unique chat key.
        return ",".join(keys)

    # Send a chat message to one or more participants.
    def send_chat_message(self, participants, message_text, private=False):
        # Ensure the sender is included in the recipients.
        participants = list(set(participants + [self.username]))
        # Determine if the chat is private (only two participants).
        is_private_chat = (len(participants) == 2)
        valid_recipients = []
        # Validate that each recipient is online.
        for r in participants:
            peer_key, info = self.get_peer_by_username(r)
            if info and info.get("status", "Online") == "Online":
                valid_recipients.append((peer_key, info))
            else:
                # If private and GUI callback exists, show a warning.
                if is_private_chat and private and self.gui_event_callback:
                    error_msg = {
                        "type": "chat",
                        "tag": "[PRIVATE]",
                        "sender": "SYSTEM",
                        "message": f"[WARNING] User {r} is offline or not found; skipping.",
                        "recipients": participants
                    }
                    self.gui_event_callback(error_msg)
                elif is_private_chat:
                    self._output(f"[WARNING] User {r} is offline or not found; skipping.")
        # If no valid recipients remain, report it.
        if not valid_recipients:
            if is_private_chat and private and self.gui_event_callback:
                error_msg = {
                    "type": "chat",
                    "tag": "[PRIVATE]",
                    "sender": "SYSTEM",
                    "message": "[WARNING] No valid recipients online.",
                    "recipients": participants
                }
                self.gui_event_callback(error_msg)
            else:
                self._output("[INFO] No valid recipients online.")
            return
        # Prepare the chat message based on privacy.
        if private:
            chat_key = self.get_chat_key(participants)
            tag = "[PRIVATE]" if is_private_chat else "[GROUP]"
            chat_msg = {
                "type": "chat",
                "sender": self.username,
                "message": message_text,
                "tag": tag,
                "recipients": participants,
                "chat_key": chat_key
            }
        else:
            chat_msg = {
                "type": "chat",
                "sender": self.username,
                "message": message_text,
                "tag": "[CHAT]"
            }
        # Send the message to each valid recipient.
        for peer_key, info in valid_recipients:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(chat_msg).encode('utf-8'))
                s.close()
            except Exception as e:
                # If sending fails, report the error.
                if is_private_chat and private and self.gui_event_callback:
                    error_msg = {
                        "type": "chat",
                        "tag": "[PRIVATE]",
                        "sender": "SYSTEM",
                        "message": f"[ERROR] Could not send message to {info['username']} - {e}",
                        "recipients": participants
                    }
                    self.gui_event_callback(error_msg)
                elif is_private_chat:
                    self._output(f"[ERROR] Could not send message to {info['username']} - {e}")

    # Broadcast a public chat message to all online peers.
    def broadcast_chat_message(self, message_text):
        # Prepare a standard chat message.
        chat_msg = {
            "type": "chat",
            "sender": self.username,
            "message": message_text,
            "tag": "[CHAT]"
        }
        with self.lock:
            peers = list(self.dht.items())
        # Send the message to every peer except self and offline peers.
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

    # Send a file to a specific peer.
    def send_file(self, target_username, file_path, private=False, chat_key=None, recipients=None):
        # Check if the file exists.
        if not os.path.exists(file_path):
            self._output(f"[ERROR] File '{file_path}' does not exist.")
            return
        # Get the absolute path of the file.
        file_path = os.path.abspath(file_path)
        print(f"DEBUG: Sending file from: {file_path} (exists: {os.path.exists(file_path)})")
        # Retrieve the target peer's details.
        peer_key, info = self.get_peer_by_username(target_username)
        if info:
            # If the target peer is offline, warn the user.
            if info.get("status", "Online") != "Online":
                self._output(f"[WARNING] User {target_username} is offline; file not sent.")
                return
            try:
                # Read the file as bytes.
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
                # Encode the file data in base64.
                file_data = base64.b64encode(file_bytes).decode('utf-8')
                # Prepare the file message.
                file_msg = {
                    "type": "file",
                    "sender": self.username,
                    "filename": os.path.basename(file_path),
                    "data": file_data
                }
                # For private transfers, add additional tags.
                if private and chat_key:
                    file_msg["tag"] = "[FILE]"
                    file_msg["chat_key"] = chat_key
                    if recipients is not None:
                        file_msg["recipients"] = recipients
                else:
                    file_msg["tag"] = "[FILE]"
                # Send the file message.
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(file_msg).encode('utf-8'))
                s.close()
                # Log the file transfer.
                self._output(f"[FILE] File '{file_path}' sent to {target_username}")
            except Exception as e:
                self._output(f"[ERROR] Could not send file to {target_username} - {e}")
        else:
            self._output(f"[WARNING] User {target_username} not found in network.")

    # Broadcast a file to all online peers.
    def broadcast_file(self, file_path):
        # Check if the file exists.
        if not os.path.exists(file_path):
            self._output(f"[ERROR] File '{file_path}' does not exist.")
            return
        try:
            # Read the file bytes.
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            # Encode the file in base64.
            file_data = base64.b64encode(file_bytes).decode('utf-8')
            # Prepare the broadcast file message.
            file_msg = {
                "type": "file",
                "sender": self.username,
                "filename": os.path.basename(file_path),
                "data": file_data,
                "tag": "[FILE]"
            }
            with self.lock:
                peers = list(self.dht.items())
            # Send the file to each online peer except self.
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

    # Disconnect this peer from the network by notifying all other peers.
    def disconnect(self):
        with self.lock:
            peers = list(self.dht.items())
        # Loop through each peer to send a leave message.
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
        # Close the server socket.
        self.server_socket.close()

    # Notify peers to open a chat window for a given set of participants.
    def notify_open_chat_window(self, participants):
        # Ensure the current user is included.
        participants = list(set(participants + [self.username]))
        # Generate a unique chat key.
        chat_key = self.get_chat_key(participants)
        # Loop through recipients except self.
        for r in participants:
            if r == self.username:
                continue
            peer_key, info = self.get_peer_by_username(r)
            if info:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect((info["host"], info["port"]))
                    msg = {
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

    # Output messages either to the GUI (if available) or to the console.
    def _output(self, message):
        if self.gui_callback:
            self.gui_callback(message)
        else:
            print(message)

    # Placeholder for a command-line interface (if needed later).
    def command_line_interface(self):
        pass


# Define the LoginWindow class using tkinter for the initial login screen.
class LoginWindow(tk.Tk):
    # Initialize the login window.
    def __init__(self):
        super().__init__()
        # Set the window title.
        self.title("Login")
        # Add a label for the username.
        tk.Label(self, text="Username:").pack(padx=10, pady=5)
        # Create a StringVar to hold the username input.
        self.username_var = tk.StringVar()
        # Add an entry widget for the username.
        tk.Entry(self, textvariable=self.username_var).pack(padx=10, pady=5)
        # Add a label for the port.
        tk.Label(self, text="Port:").pack(padx=10, pady=5)
        # Create a StringVar for the port input.
        self.port_var = tk.StringVar()
        # Add an entry widget for the port.
        tk.Entry(self, textvariable=self.port_var).pack(padx=10, pady=5)
        # Add a login button that triggers the on_login method.
        tk.Button(self, text="Login", command=self.on_login).pack(padx=10, pady=10)

    # Handle the login event.
    def on_login(self):
        # Get and strip the username and port input.
        username = self.username_var.get().strip()
        port = self.port_var.get().strip()
        # Ensure both fields are filled.
        if not username or not port:
            messagebox.showerror("Error", "Username and port are required")
            return
        try:
            # Convert port to an integer.
            port = int(port)
        except ValueError:
            messagebox.showerror("Error", "Port must be an integer")
            return
        # Close the login window.
        self.destroy()
        # Open the main GUI window.
        main_gui = MainGUI(username, port)
        main_gui.run()


# Define a class for the private chat tab within the GUI.
class PrivateChatTab(tk.Frame):
    # Initialize the private chat tab.
    def __init__(self, parent, peer, participants, chat_key):
        super().__init__(parent)
        # Store the peer object.
        self.peer = peer
        # Store the list of chat participants.
        self.participants = participants
        # Store the unique chat key.
        self.chat_key = chat_key

        # Create a scrolled text widget for the chat log.
        self.chat_log = scrolledtext.ScrolledText(self, state='disabled', wrap='word')
        self.chat_log.pack(fill=tk.BOTH, expand=True)
        # Configure text colors for different message types.
        self.chat_log.tag_configure("own", foreground="green")
        self.chat_log.tag_configure("peer", foreground="blue")
        self.chat_log.tag_configure("system", foreground="black")

        # Create a frame for the message entry area.
        self.entry_frame = tk.Frame(self)
        self.entry_frame.pack(fill=tk.X)
        # Add an entry widget for typing messages.
        self.entry = tk.Entry(self.entry_frame)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        # Bind the Return key to send messages.
        self.entry.bind("<Return>", self.send_message)
        # Add a send button to trigger message sending.
        self.send_button = tk.Button(self.entry_frame, text="Send", command=self.send_message)
        self.send_button.pack(side=tk.LEFT, padx=5)
        # Add a file button to allow uploading files.
        self.file_button = tk.Button(self.entry_frame, text="+", command=self.upload_file)
        self.file_button.pack(side=tk.LEFT, padx=5)

    # Append a new line to the chat log with a specific tag.
    def append_chat_log(self, text, tag):
        self.chat_log.configure(state='normal')
        self.chat_log.insert(tk.END, text + "\n", tag)
        self.chat_log.configure(state='disabled')
        self.chat_log.see(tk.END)

    # Handle sending a message from the chat entry.
    def send_message(self, event=None):
        msg = self.entry.get().strip()
        if msg:
            # Send the chat message as a private message.
            self.peer.send_chat_message(self.participants, msg, private=True)
            # Append the sent message to the chat log.
            self.append_chat_log(f"You: {msg}", "own")
            # Clear the entry widget.
            self.entry.delete(0, tk.END)

    # Handle uploading and sending a file.
    def upload_file(self):
        # Set the initial directory to the Downloads folder.
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        # Open a file dialog for selecting a file.
        filepath = filedialog.askopenfilename(initialdir=downloads_dir)
        if filepath:
            # Send the file to all recipients except self.
            for recipient in self.participants:
                if recipient != self.peer.username:
                    self.peer.send_file(recipient, filepath, private=True, chat_key=self.chat_key, recipients=self.participants)
            # Append a system message indicating the file was sent.
            self.append_chat_log(f"[FILE] File sent: {os.path.basename(filepath)}", "system")


# Define the main GUI class for the peer-to-peer chat application.
class MainGUI:
    # Initialize the main GUI.
    def __init__(self, username, port):
        # Store the username and port.
        self.username = username
        self.port = port
        # Create a Peer instance.
        self.peer = Peer(username, host='127.0.0.1', port=port)
        # Start the peer server to listen for connections.
        self.peer.start_server()
        # Create a queue for incoming messages.
        self.msg_queue = queue.Queue()
        # Set up GUI callbacks in the Peer instance.
        self.peer.gui_callback = self.enqueue_message
        self.peer.gui_event_callback = self.handle_peer_event

        # Dictionary to manage private chat tabs.
        self.private_chats = {}

        # Create the root tkinter window.
        self.root = tk.Tk()
        # Set the window title with connection info.
        self.root.title(f"Peer Chat - {username}@127.0.0.1:{port}")

        # Create a left frame for controls.
        self.left_frame = tk.Frame(self.root, width=200)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y)

        # Create a button showing the current username.
        self.username_button = tk.Button(self.left_frame, text=username, command=self.open_username_menu)
        self.username_button.pack(pady=5)
        # Create a button to open the connect window.
        self.connect_button = tk.Button(self.left_frame, text="Connect", command=self.open_connect_window)
        self.connect_button.pack(pady=5)
        # Label for the peers list.
        tk.Label(self.left_frame, text="Peers:").pack(pady=5)
        # Frame to display the list of connected peers.
        self.peers_list_frame = tk.Frame(self.left_frame)
        self.peers_list_frame.pack(fill=tk.BOTH, expand=True)
        # Button to open a message window.
        self.message_button = tk.Button(self.left_frame, text="Message", command=self.open_message_window)
        self.message_button.pack(side=tk.BOTTOM, pady=5)

        # Create a frame for public chat messages.
        self.public_frame = tk.Frame(self.root)
        self.public_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Create a scrolled text widget for the public chat log.
        self.chat_log = scrolledtext.ScrolledText(self.public_frame, state='disabled', wrap='word')
        self.chat_log.pack(fill=tk.BOTH, expand=True)
        # Configure tag colors for different message types.
        self.chat_log.tag_configure("own", foreground="green")
        self.chat_log.tag_configure("peer", foreground="blue")
        self.chat_log.tag_configure("system", foreground="black")
        # Display initial connection information and available commands.
        initial_info = f"Working Directory: {os.getcwd()}\nAddress: 127.0.0.1:{self.port}\nCommands:\n" \
                       "/connect <ip> <port>\n/update <new_username>\n/msg <user1,user2,...> <message>\n" \
                       "/sendfile <user1,user2,...> <filepath>\n/exit\n"
        self.append_chat_log(initial_info, "system")
        # Create a frame for command input.
        self.entry_frame = tk.Frame(self.public_frame)
        self.entry_frame.pack(fill=tk.X)
        # Create an entry widget for commands.
        self.command_entry = tk.Entry(self.entry_frame)
        self.command_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        # Bind the Return key to process commands.
        self.command_entry.bind("<Return>", self.process_command)
        # Create a send button for commands.
        self.send_button = tk.Button(self.entry_frame, text="Send", command=self.process_command)
        self.send_button.pack(side=tk.LEFT, padx=5)
        # Create a file button to allow file uploads in public chat.
        self.file_button = tk.Button(self.entry_frame, text="+", command=self.upload_file_public)
        self.file_button.pack(side=tk.LEFT, padx=5)

        # Create tabbed chat interface for private/group chats.
        self.chat_tabs = ttk.Notebook(self.root)
        self.chat_tabs.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    # Generate a chat key based on chat participants.
    def get_chat_key(self, participants):
        participants = list(set(participants + [self.peer.username]))
        return self.peer.get_chat_key(participants)

    # Enqueue messages received from the peer object.
    def enqueue_message(self, message):
        self.msg_queue.put(message)

    # Process messages in the queue and update the public chat log.
    def process_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                tag = "peer"
                # Determine the tag based on message type.
                if msg.startswith("[INFO]") or msg.startswith("[WARNING]") or msg.startswith("[ERROR]") or msg.startswith("[FILE]"):
                    tag = "system"
                if msg.startswith("[CHAT]"):
                    msg = msg.replace("[CHAT] ", "", 1)
                # Skip private or group messages here.
                if msg.startswith("[PRIVATE]") or msg.startswith("[GROUP]"):
                    continue
                # Append the message to the public chat log.
                self.append_chat_log(msg, tag)
        except queue.Empty:
            pass
        # Schedule the next queue check.
        self.root.after(100, self.process_queue)

    # Append text to the public chat log.
    def append_chat_log(self, text, tag=None):
        self.chat_log.configure(state='normal')
        self.chat_log.insert(tk.END, text + "\n", tag)
        self.chat_log.configure(state='disabled')
        self.chat_log.see(tk.END)

    # Process commands entered by the user.
    def process_command(self, event=None):
        command = self.command_entry.get().strip()
        if command:
            # If the command starts with a forward slash, treat it as a special command.
            if command.startswith("/"):
                parts = command.split(" ", 1)
                cmd = parts[0]
                # Handle the connect command.
                if cmd == "/connect":
                    if len(parts) < 2:
                        self.append_chat_log("[WARNING] Usage: /connect <ip> <port>", "system")
                    else:
                        args = parts[1].split()
                        if len(args) == 2:
                            ip = args[0]
                            try:
                                port = int(args[1])
                                self.peer.connect_to_peer(ip, port)
                            except ValueError:
                                self.append_chat_log("[ERROR] Port must be an integer.", "system")
                        else:
                            self.append_chat_log("[WARNING] Usage: /connect <ip> <port>", "system")
                # Handle the update username command.
                elif cmd == "/update":
                    if len(parts) < 2:
                        self.append_chat_log("[WARNING] Usage: /update <new_username>", "system")
                    else:
                        new_username = parts[1].strip()
                        self.peer.update_username(new_username)
                        self.username_button.config(text=new_username)
                # Handle the private/group message command.
                elif cmd == "/msg":
                    if len(parts) < 2 or " " not in parts[1]:
                        self.append_chat_log("[WARNING] Usage: /msg <user1,user2,...> <message>", "system")
                    else:
                        recipients_str, message_text = parts[1].split(" ", 1)
                        recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
                        if recipients:
                            recipients = list(set(recipients + [self.peer.username]))
                            chat_key = self.get_chat_key(recipients)
                            if chat_key in self.private_chats:
                                chat_tab = self.private_chats[chat_key]
                            else:
                                self.open_group_chat(recipients)
                                chat_tab = self.private_chats[self.get_chat_key(recipients)]
                            chat_tab.append_chat_log(f"You: {message_text}", "own")
                            self.peer.send_chat_message(recipients, message_text, private=True)
                        else:
                            self.append_chat_log("[WARNING] No valid recipients provided.", "system")
                # Handle the send file command.
                elif cmd == "/sendfile":
                    if len(parts) < 2 or " " not in parts[1]:
                        self.append_chat_log("[WARNING] Usage: /sendfile <user1,user2,...> <filepath>", "system")
                    else:
                        recipients_str, filepath = parts[1].split(" ", 1)
                        recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
                        if recipients:
                            recipients = list(set(recipients + [self.peer.username]))
                            chat_key = self.get_chat_key(recipients)
                            for recipient in recipients:
                                if recipient != self.peer.username:
                                    self.peer.send_file(recipient, filepath.strip(), private=True, chat_key=chat_key, recipients=recipients)
                        else:
                            self.append_chat_log("[WARNING] No valid recipients provided.", "system")
                # Handle the exit command.
                elif cmd == "/exit":
                    self.peer.disconnect()
                    self.root.destroy()
                    sys.exit(0)
                # Handle unknown commands.
                else:
                    self.append_chat_log("[WARNING] Unknown command.", "system")
            else:
                # If no command prefix, broadcast as a public message.
                self.peer.broadcast_chat_message(command)
                self.append_chat_log(f"You: {command}", "own")
            # Clear the command entry after processing.
            self.command_entry.delete(0, tk.END)

    # Allow uploading a file to broadcast publicly.
    def upload_file_public(self):
        filed = filedialog.askopenfilename()
        if filed:
            self.peer.broadcast_file(filed)
            self.append_chat_log(f"[FILE] Public file sent: {os.path.basename(filed)}", "system")

    # Update the list of peers shown in the GUI.
    def update_peers_list(self):
        # Remove all existing peer buttons.
        for widget in self.peers_list_frame.winfo_children():
            widget.destroy()
        with self.peer.lock:
            # For each peer, create a button to start a private chat.
            for key, info in self.peer.dht.items():
                if key == self.peer.key:
                    continue
                username = info.get("username", "Unknown")
                status = info.get("status", "Offline")
                color = "green" if status == "Online" else "red"
                btn = tk.Button(
                    self.peers_list_frame,
                    text=username,
                    fg=color,
                    command=lambda u=username: self.open_private_chat(u)
                )
                btn.pack(fill=tk.X, padx=5, pady=2)
        # Schedule the next update of the peers list.
        self.root.after(5000, self.update_peers_list)

    # Open a window to connect to a new peer.
    def open_connect_window(self):
        win = tk.Toplevel(self.root)
        win.title("Connect to Peer")
        tk.Label(win, text="Peer IP:Port").pack(pady=5)
        entry = tk.Entry(win)
        entry.pack(pady=5)
        # Function to process the connection input.
        def connect():
            data = entry.get().strip()
            if ":" in data:
                ip, port_str = data.split(":", 1)
                try:
                    port = int(port_str)
                    self.peer.connect_to_peer(ip.strip(), port)
                    win.destroy()
                except ValueError:
                    messagebox.showerror("Error", "Port must be an integer")
            else:
                messagebox.showerror("Error", "Input must be in the format IP:Port")
        tk.Button(win, text="Connect", command=connect).pack(pady=10)

    # Open a menu to change the username or logout.
    def open_username_menu(self):
        win = tk.Toplevel(self.root)
        win.title("Username Options")
        tk.Label(win, text=f"Current Username: {self.peer.username}").pack(pady=5)
        # Function to change username.
        def change_username():
            win.destroy()
            self.open_change_username_window()
        # Function to logout.
        def logout():
            self.peer.disconnect()
            self.root.destroy()
            sys.exit(0)
        tk.Button(win, text="Change Username", command=change_username).pack(pady=5)
        tk.Button(win, text="Logout", command=logout).pack(pady=5)
        tk.Button(win, text="Cancel", command=win.destroy).pack(pady=5)

    # Open a window to change the username.
    def open_change_username_window(self):
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
        tk.Button(win, text="Change", command=change).pack(pady=10)

    # Open a window to select peers for messaging.
    def open_message_window(self):
        win = tk.Toplevel(self.root)
        win.title("Select Peers for Message")
        selections = {}
        with self.peer.lock:
            # Create a checkbox for each peer.
            for key, info in self.peer.dht.items():
                if key == self.peer.key:
                    continue
                var = tk.BooleanVar()
                selections[info["username"]] = var
                tk.Checkbutton(win, text=info["username"], variable=var).pack(anchor='w')
        # Confirm the selection and open a group chat.
        def confirm():
            selected = [username for username, i in selections.items() if i.get()]
            if selected:
                selected = list(set(selected + [self.peer.username]))
                win.destroy()
                self.open_group_chat(selected)
            else:
                messagebox.showerror("Error", "No peers selected")
        tk.Button(win, text="Confirm", command=confirm).pack(pady=10)

    # Open or create a group chat tab.
    def open_group_chat(self, participants):
        participants = list(set(participants + [self.peer.username]))
        chat_key = self.get_chat_key(participants)
        if chat_key in self.private_chats:
            self.chat_tabs.select(self.private_chats[chat_key])
        else:
            chat_tab = PrivateChatTab(self.chat_tabs, self.peer, participants, chat_key)
            self.private_chats[chat_key] = chat_tab
            other_names = sorted([name for name in participants if name != self.peer.username])
            tab_title = ", ".join(other_names)
            self.chat_tabs.add(chat_tab, text=tab_title)
            self.peer.notify_open_chat_window(participants)
            self.chat_tabs.select(chat_tab)

    # Open a private chat with a selected peer.
    def open_private_chat(self, username):
        participants = list({username, self.peer.username})
        chat_key = self.get_chat_key(participants)
        if chat_key in self.private_chats:
            self.chat_tabs.select(self.private_chats[chat_key])
        else:
            chat_tab = PrivateChatTab(self.chat_tabs, self.peer, participants, chat_key)
            self.private_chats[chat_key] = chat_tab
            other_names = sorted([name for name in participants if name != self.peer.username])
            tab_title = ", ".join(other_names)
            self.chat_tabs.add(chat_tab, text=tab_title)
            self.peer.notify_open_chat_window(participants)
            self.chat_tabs.select(chat_tab)

    # Handle events received from peers (e.g., chat and file messages).
    def handle_peer_event(self, message):
        if message.get("type") == "control" and message.get("control") == "open_chat_window":
            participants = message.get("recipients", [])
            chat_key = message.get("chat_key", self.get_chat_key(participants))
            if chat_key not in self.private_chats:
                self.open_group_chat(participants)
            else:
                self.chat_tabs.select(self.private_chats[chat_key])
        elif message.get("type") == "chat":
            tag = message.get("tag", "[CHAT]")
            if tag in ("[PRIVATE]", "[GROUP]"):
                chat_key = message.get("chat_key")
                sender = message.get("sender")
                msg_text = message.get("message")
                if chat_key in self.private_chats:
                    chat_tab = self.private_chats[chat_key]
                    if sender != self.peer.username:
                        chat_tab.append_chat_log(f"{sender}: {msg_text}", "peer")
                else:
                    participants = message.get("recipients", [])
                    self.open_group_chat(participants)
                    chat_key = self.get_chat_key(participants)
                    if chat_key in self.private_chats:
                        chat_tab = self.private_chats[chat_key]
                        if sender != self.peer.username:
                            chat_tab.append_chat_log(f"{sender}: {msg_text}", "peer")
        elif message.get("type") == "file":
            if "chat_key" in message:
                chat_key = message.get("chat_key")
                sender = message.get("sender")
                filename = message.get("filename")
                if chat_key in self.private_chats:
                    chat_tab = self.private_chats[chat_key]
                    chat_tab.append_chat_log(f"[FILE] Received '{filename}' from {sender}", "peer")
                else:
                    recipients = message.get("recipients", [])
                    if recipients:
                        self.open_group_chat(recipients)
                        chat_key = self.get_chat_key(recipients)
                        if chat_key in self.private_chats:
                            chat_tab = self.private_chats[chat_key]
                            chat_tab.append_chat_log(f"[FILE] Received '{filename}' from {sender}", "peer")
                    else:
                        self.append_chat_log(f"[FILE] Received '{filename}' from {sender}", "system")

    # Start the GUI main loop and set up periodic tasks.
    def run(self):
        self.root.after(100, self.process_queue)
        self.root.after(5000, self.update_peers_list)
        self.root.mainloop()


# Define the main entry point of the application.
def main():
    # Create the login window.
    login = LoginWindow()
    # Start the login window's event loop.
    login.mainloop()


# Only run the main function if this file is executed as the main program.
if __name__ == "__main__":
    main()
