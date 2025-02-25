import socket
import threading
import json
import base64
import os
import sys


class Peer:
    def __init__(self, username, host='127.0.0.1', port=5000):
        self.username = username
        self.host = host
        self.port = port

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)

        self.dht = {self.username: {"host": self.host, "port": self.port, "status": "Online"}}
        self.lock = threading.Lock()

    def start_server(self):
        threading.Thread(target=self.listen_for_connections, daemon=True).start()

    def listen_for_connections(self):
        print(f"[INFO] {self.username} is listening on {self.host}:{self.port}.")

        while True:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_connection, args=(conn, addr), daemon=True).start()
            except Exception as e:
                print(f"[ERROR] Error accepting connections: {e}")
                continue

    def handle_connection(self, conn, addr):
        while True:
            try:
                data = conn.recv(4096)

                if not data:
                    break

                message = json.loads(data.decode('utf-8'))
                self.process_message(message, conn)

            except Exception as e:
                if isinstance(e, OSError) and e.errno == 10053:
                    break

                print(f"[ERROR] Error handling connection from {addr}: {e}")
                break

        conn.close()

    def broadcast_peer_list(self):
        with self.lock:
            peer_list_message = {
                                "type": "peer_list",
                                "peers": self.dht
                                }

            for target_username, info in list(self.dht.items()):
                if target_username == self.username:
                    continue

                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect((info["host"], info["port"]))
                    s.send(json.dumps(peer_list_message).encode('utf-8'))
                    s.close()
                except Exception as e:
                    print(f"[ERROR] Could not send updated peer list to {target_username} - {e}")

    def process_message(self, message, conn=None):
        msg_type = message.get("type")

        if msg_type == "control":
            control_type = message.get("control")

            if control_type == "join":
                new_username = message.get("sender")
                new_host = message.get("host")
                new_port = message.get("port")

                with self.lock:
                    self.dht[new_username] = {"host": new_host, "port": new_port, "status": "Online"}

                print(f"[INFO] {new_username} joined from {new_host}:{new_port}.")
                self.broadcast_peer_list()

                if conn is not None:
                    with self.lock:
                        reply_peers = self.dht.copy()

                    try:
                        conn.send(json.dumps({"type": "peer_list", "peers": reply_peers}).encode('utf-8'))
                    except Exception as e:
                        print(f"[ERROR] Could not send peer list to {new_username} - {e}.")

            elif control_type == "update":
                old_username = message.get("sender")
                new_username = message.get("new_username")
                new_host = message.get("host")
                new_port = message.get("port")

                with self.lock:
                    keys_to_update = [user for user, info in self.dht.items()
                                      if info.get("host") == new_host and info.get("port") == new_port]

                    for key in keys_to_update:
                        if key != new_username:
                            self.dht[new_username] = self.dht.pop(key)
                            self.dht[new_username].update({"host": new_host, "port": new_port, "status": "Online"})

                print(f"[INFO] {old_username} changed username to {new_username}.")

            elif control_type == "leave":
                leave_username = message.get("sender")
                updated = False

                with self.lock:
                    if leave_username in self.dht and self.dht[leave_username].get("status", "Online") != "Offline":
                        self.dht[leave_username]["status"] = "Offline"
                        updated = True

                if updated:
                    print(f"[INFO] {leave_username} has left the network.")

        elif msg_type == "peer_list":
            peers = message.get("peers", {})

            with self.lock:
                for user in list(self.dht.keys()):
                    if user not in peers and user != self.username:
                        print(f"[INFO] Removing outdated username: {user}")
                        del self.dht[user]

                self.dht.update(peers)

        elif msg_type == "chat":
            sender = message.get("sender")
            chat_message = message.get("message")
            tag = message.get("tag", "[CHAT]")
            print(f"{tag} {sender}: {chat_message}")

        elif msg_type == "file":
            sender = message.get("sender")
            filename = message.get("filename")
            file_data = message.get("data")
            decoded_data = base64.b64decode(file_data.encode('utf-8'))

            with open(f"received_{filename}", "wb") as f:
                f.write(decoded_data)

            print(f"[FILE] Received file '{filename}' from {sender}")

        else:
            print("[WARNING] Received unknown message type.")

    def forward_message(self, message):
        with self.lock:
            peers = list(self.dht.items())

        for target_username, info in peers:
            if target_username == self.username:
                continue

            if message.get("control") != "update" and info.get("status", "Online") != "Online":
                continue

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(message).encode('utf-8'))
                s.close()
            except Exception as e:
                print(f"[ERROR] Could not forward message to {target_username} - {e}")

    def connect_to_peer(self, peer_ip, peer_port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((peer_ip, peer_port))

            join_msg =  {
                        "type": "control",
                        "control": "join",
                        "sender": self.username,
                        "host": self.host,
                        "port": self.port
                        }

            s.send(json.dumps(join_msg).encode('utf-8'))
            data = s.recv(4096)

            if data:
                response = json.loads(data.decode('utf-8'))

                if response.get("type") == "peer_list":
                    with self.lock:
                        self.dht.update(response.get("peers", {}))
                        print(f"[INFO] Received peer list:", list(self.dht.keys()))

                    self.broadcast_peer_list()

                print(f"[INFO] Connected to peer at {peer_ip}:{peer_port}")

            s.close()

        except Exception as e:
            print(f"[ERROR] Could not connect to peer at {peer_ip}:{peer_port} - {e}")

    def update_username(self, new_username):
        old_username = self.username
        old_host, old_port = self.host, self.port

        with self.lock:
            self.username = new_username

            keys_to_update = [user for user, info in self.dht.items()
                              if info.get("host") == old_host and info.get("port") == old_port]

            for key in keys_to_update:
                self.dht[new_username] = self.dht.pop(key)
                self.dht[new_username].update({"host": self.host, "port": self.port, "status": "Online"})

        update_msg =    {
                        "type": "control",
                        "control": "update",
                        "sender": old_username,
                        "new_username": new_username,
                        "host": self.host,
                        "port": self.port
                        }

        self.forward_message(update_msg)
        print(f"[INFO] Username updated from {old_username} to {new_username}.")

    def send_chat_message(self, recipients, message_text):
        valid_recipients = []

        with self.lock:
            for r in recipients:
                info = self.dht.get(r)

                if info and info.get("status", "Online") == "Online":
                    valid_recipients.append(r)
                else:
                    print(f"[WARNING] User {r} is offline or not found; skipping.")

        if not valid_recipients:
            print("[WARNING] No valid recipients online.")
            return

        if len(valid_recipients) == 1:
            tag = "[PRIVATE]"
        else:
            tag = "[GROUP: " + ", ".join(valid_recipients) + "]"

        chat_msg =  {
                    "type": "chat",
                    "sender": self.username,
                    "message": message_text,
                    "tag": tag,
                    "recipients": valid_recipients
                    }

        for recipient in valid_recipients:
            with self.lock:
                info = self.dht.get(recipient)

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(chat_msg).encode('utf-8'))
                s.close()
            except Exception as e:
                print(f"[ERROR] Could not send message to {recipient} - {e}")

    def broadcast_chat_message(self, message_text):
        chat_msg =  {
                    "type": "chat",
                    "sender": self.username,
                    "message": message_text,
                    "tag": "[CHAT]"
                    }

        with self.lock:
            peers = list(self.dht.items())

        for target_username, info in peers:
            if target_username == self.username:
                continue

            if info.get("status", "Online") != "Online":
                continue

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(chat_msg).encode('utf-8'))
                s.close()
            except Exception as e:
                print(f"[ERROR] Could not send message to {target_username} - {e}")

    def send_file(self, target_username, file_path):
        if not os.path.exists(file_path):
            print(f"[ERROR] File '{file_path}' does not exist.")
            return

        with self.lock:
            info = self.dht.get(target_username)

        if info:
            if info.get("status", "Online") != "Online":
                print(f"[WARNING] User {target_username} is offline; file not sent.")
                return

            try:
                with open(file_path, "rb") as f:
                    file_bytes = f.read()

                file_data = base64.b64encode(file_bytes).decode('utf-8')

                file_msg =  {
                            "type": "file",
                            "sender": self.username,
                            "filename": os.path.basename(file_path),
                            "data": file_data
                            }

                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))
                s.send(json.dumps(file_msg).encode('utf-8'))
                s.close()

                print(f"[FILE] File '{file_path}' sent to {target_username}")

            except Exception as e:
                print(f"[ERROR] Could not send file to {target_username} - {e}")

        else:
            print(f"[WARNING] User {target_username} not found in network.")

    def disconnect(self):
        with self.lock:
            peers = list(self.dht.items())

        for target_username, info in peers:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((info["host"], info["port"]))

                leave_msg = {
                            "type": "control",
                            "control": "leave",
                            "sender": self.username
                            }

                s.send(json.dumps(leave_msg).encode('utf-8'))
                s.close()

            except Exception as e:
                print(f"[ERROR] Could not send disconnect message to {target_username} - {e}")

        self.server_socket.close()

    def command_line_interface(self):
        print("\nCommands:")

        print("/connect <ip> <port>                         - Connect to a peer's port")
        print("/users                                       - List known peers with online/ offline status")
        print("/update <new_username>                       - Change your username")
        print("/msg <user1,user2,...> <message>             - Send a message to one or more users")
        print("/sendfile <user1,user2,...> <filepath>       - Send a file to one or more users")
        print("/exit                                        - Exit and go offline")

        print("\nNote: Any input not starting with '/' is broadcast to all online peers.")

        while True:
            try:
                command = input(">> ").strip()

                if not command:
                    continue

                if command.startswith("/"):
                    parts = command.split(" ", 1)
                    cmd = parts[0]

                    if cmd == "/connect":
                        if len(parts) < 2:
                            print("[WARNING] Usage: /connect <ip> <port>")
                        else:
                            args = parts[1].split()

                            if len(args) == 2:
                                ip = args[0]
                                port = int(args[1])
                                self.connect_to_peer(ip, port)
                            else:
                                print("[WARNING] Usage: /connect <ip> <port>")

                    elif cmd == "/users":
                        with self.lock:
                            print("Known peers:")

                            for user, info in self.dht.items():
                                if user == self.username:
                                    continue

                                print(f"{user} ({info.get('status', 'Unknown')}) at {info['host']}:{info['port']}")

                    elif cmd == "/update":
                        if len(parts) < 2:
                            print("[WARNING] Usage: /update <new_username>")
                        else:
                            new_username = parts[1].strip()
                            self.update_username(new_username)

                    elif cmd == "/msg":
                        if len(parts) < 2 or " " not in parts[1]:
                            print("[WARNING] Usage: /msg <user1,user2,...> <message>")
                        else:
                            recipients_str, message_text = parts[1].split(" ", 1)
                            recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]

                            if recipients:
                                self.send_chat_message(recipients, message_text)
                            else:
                                print("[WARNING] No valid recipients provided.")

                    elif cmd == "/sendfile":
                        if len(parts) < 2 or " " not in parts[1]:
                            print("[WARNING] Usage: /sendfile <user1,user2,...> <filepath>")
                        else:
                            recipients_str, filepath = parts[1].split(" ", 1)
                            recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]

                            if recipients:
                                for recipient in recipients:
                                    self.send_file(recipient, filepath.strip())
                            else:
                                print("[WARNING] No valid recipients provided.")

                    elif cmd == "/exit":
                        self.disconnect()
                        sys.exit(0)

                    else:
                        print("[WARNING] Unknown command. Please try again.")

                else:
                    self.broadcast_chat_message(command)

            except Exception as e:
                print(f"[ERROR] {e}")

def main():
    print("Current working directory:", os.getcwd())

    username = input("\nEnter your username: ")
    port = int(input("Enter port to listen on: "))

    peer = Peer(username, host='127.0.0.1', port=port)
    peer.start_server()
    peer.command_line_interface()

if __name__ == "__main__":
    main()
