#!/usr/bin/env python3
"""
Stdio ↔ TCP bridge for MCP. Claude Code's MCP client speaks stdio; lldb's
MCP server only speaks Unix-socket / TCP. This bridge runs as a stdio MCP
child for Claude and forwards bytes both ways to a TCP MCP server (lldb).

Usage in Claude's MCP config:
    claude mcp add lldb-mcp -- python3 \\
        /path/to/misc/mcp_stdio_tcp_bridge.py 127.0.0.1 9999

The lldb MCP server should already be listening on 127.0.0.1:9999 (start
via `protocol-server start MCP listen://127.0.0.1:9999` from inside an
interactive lldb session).
"""
import sys
import socket
import select
import os


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <host> <port>", file=sys.stderr)
        sys.exit(2)
    host, port = sys.argv[1], int(sys.argv[2])

    # Connect to lldb's MCP TCP server.
    try:
        s = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        print(f"bridge: cannot connect to {host}:{port}: {e}", file=sys.stderr)
        sys.exit(1)
    s.setblocking(False)

    # Switch stdin/stdout to non-blocking, raw bytes.
    sin = sys.stdin.buffer.fileno()
    sout = sys.stdout.buffer.fileno()
    os.set_blocking(sin, False)
    os.set_blocking(sout, False)

    out_buf = b""  # bytes pending to write to stdout
    net_buf = b""  # bytes pending to write to socket

    sock_open = True
    stdin_open = True

    while sock_open or stdin_open or out_buf or net_buf:
        rlist = []
        wlist = []
        if stdin_open: rlist.append(sin)
        if sock_open: rlist.append(s.fileno())
        if out_buf: wlist.append(sout)
        if net_buf and sock_open: wlist.append(s.fileno())

        if not rlist and not wlist:
            break

        r, w, _ = select.select(rlist, wlist, [], 0.5)

        if sin in r:
            try:
                chunk = os.read(sin, 8192)
                if not chunk:
                    stdin_open = False
                    # Close socket write side so lldb sees EOF.
                    try: s.shutdown(socket.SHUT_WR)
                    except Exception: pass
                else:
                    net_buf += chunk
            except BlockingIOError:
                pass
            except OSError:
                stdin_open = False

        if sock_open and s.fileno() in r:
            try:
                chunk = s.recv(8192)
                if not chunk:
                    sock_open = False
                else:
                    out_buf += chunk
            except BlockingIOError:
                pass
            except OSError:
                sock_open = False

        if sout in w and out_buf:
            try:
                n = os.write(sout, out_buf)
                out_buf = out_buf[n:]
            except BlockingIOError:
                pass
            except OSError:
                # Stdout closed — Claude has gone away.
                break

        if sock_open and s.fileno() in w and net_buf:
            try:
                n = s.send(net_buf)
                net_buf = net_buf[n:]
            except BlockingIOError:
                pass
            except OSError:
                sock_open = False


if __name__ == "__main__":
    main()
