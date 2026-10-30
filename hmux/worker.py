"""
HMUX Background Daemon Worker
Spawns a target harness inside a dedicated PTY, logs output to disk,
and serves a UNIX domain socket for live interactive attachment and control.
"""

import os
import sys
import pty
import fcntl
import struct
import select
import socket
import signal
import psutil
import termios
from pathlib import Path
from typing import List, Dict

def run_worker(
    session_id: str,
    cmd: List[str],
    cwd: str,
    env: Dict[str, str],
    log_path: str,
    socket_path: str
):
    """
    Spawns cmd in a pseudoterminal (PTY), piping output to log_path and unix socket.
    """
    # Remove old socket if exists
    if os.path.exists(socket_path):
        try:
            os.unlink(socket_path)
        except OSError:
            pass

    # Create UNIX domain socket for client attachment
    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(socket_path)
    server_sock.listen(5)
    server_sock.setblocking(False)

    master_fd, slave_fd = pty.openpty()

    # Set standard initial terminal window size (35 rows, 120 columns)
    # Required for TUIs like Codex / Ratatui / Rich to render correctly
    winsize = struct.pack("HHHH", 35, 120, 0, 0)
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
    except Exception:
        pass

    # Fork child process
    pid = os.fork()
    if pid == 0:
        # Child process: inside PTY
        os.setsid()
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        except Exception:
            pass

        os.close(master_fd)
        server_sock.close()

        # Connect stdio to slave PTY
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)

        os.chdir(cwd)

        # Ensure terminal environment variables
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        # Execute command
        os.execvpe(cmd[0], cmd, env)
        sys.exit(1)

    # Parent process (daemon worker)
    os.close(slave_fd)

    # Open log file for appending
    log_file = open(log_path, "ab", buffering=0)

    # Store child PID in a pidfile next to socket
    pid_file = socket_path + ".pid"
    with open(pid_file, "w") as f:
        f.write(str(pid))

    attached_clients: List[socket.socket] = []

    def cleanup(signum=None, frame=None):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        server_sock.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        if os.path.exists(pid_file):
            os.unlink(pid_file)
        log_file.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    try:
        while True:
            # Check if child process is still alive
            status = os.waitpid(pid, os.WNOHANG)
            if status != (0, 0):
                print(f"[worker] Child {pid} exited: status={status}", flush=True)
                break

            readable_sockets = [server_sock] + attached_clients
            rlist, _, _ = select.select([master_fd] + readable_sockets, [], [], 0.05)

            for s in rlist:
                if s == master_fd:
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        log_file.write(data)
                        # Broadcast to attached clients
                        dead_clients = []
                        for client in attached_clients:
                            try:
                                client.sendall(data)
                            except Exception:
                                dead_clients.append(client)
                        for d in dead_clients:
                            attached_clients.remove(d)
                            d.close()
                    except OSError:
                        pass

                elif s == server_sock:
                    try:
                        client_sock, _ = server_sock.accept()
                        client_sock.setblocking(False)
                        attached_clients.append(client_sock)
                    except OSError:
                        pass

                elif s in attached_clients:
                    try:
                        input_data = s.recv(4096)
                        if not input_data:
                            attached_clients.remove(s)
                            s.close()
                        else:
                            # Forward user input to harness PTY master
                            os.write(master_fd, input_data)
                    except OSError:
                        attached_clients.remove(s)
                        s.close()

    finally:
        cleanup()

if __name__ == "__main__":
    if len(sys.argv) < 6:
        sys.exit(1)
    # Args: session_id cwd log_path socket_path cmd...
    s_id = sys.argv[1]
    s_cwd = sys.argv[2]
    s_log = sys.argv[3]
    s_sock = sys.argv[4]
    s_cmd = sys.argv[5:]

    s_env = os.environ.copy()
    s_env["TERM"] = "xterm-256color"
    s_env["COLORTERM"] = "truecolor"

    run_worker(s_id, s_cmd, s_cwd, s_env, s_log, s_sock)
