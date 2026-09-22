"""Exercise one exact AppKit menu item through the production socket ABI.

Invoke with python3 rather than executing this file directly; chroot AMFI
rejects shebang scripts.  The tool is intentionally read-only unless
``--perform`` is supplied.
"""

import argparse
import os
import socket
import struct
import time

from macws_window_metrics_dump import read_metrics


MAGIC = 0x4D4E574D
VERSION = 2
REQUEST = struct.Struct("<IHHHHQiIQQ")
HEADER = struct.Struct("<IHHHHQiIQIIII")
NODE = struct.Struct("<QQIIiIIII")


def require_live_target(pid: int) -> None:
    if pid <= 1:
        raise RuntimeError('refusing application action for invalid/system PID')
    try:
        os.kill(pid, 0)
    except OSError as error:
        raise RuntimeError(f'refusing action: target pid={pid} is not observable/alive') from error


def require_focused_target(pid: int, window_id: int) -> None:
    """Fail closed before actions; the menu ABI does not select a responder.

    Activate through the real Host/iPadOS UI first. This is a point-in-time
    guard, not an atomic action/completion witness: do not switch windows
    during a probe, and verify the native result independently afterwards.
    The sidecar is change-driven, so its mtime is not a heartbeat.
    """
    # A crashed application's change-driven sidecar can survive its process.
    # Its old focused bit must not authorize a new global pointer operation.
    # This is a liveness guard, not an atomic responder/identity guarantee.
    require_live_target(pid)
    name = f"macws_window_metrics.{pid}.bin"
    host_path = f"/var/mnt/rootfs/private/tmp/{name}"
    path = host_path if os.path.exists(host_path) else f"/private/tmp/{name}"
    metrics = read_metrics(path)
    focused = [entry["window_id"] for entry in metrics["windows"]
               if "focused" in entry["flags"]]
    if metrics["owner_pid_from_filename"] != pid or focused != [window_id]:
        raise RuntimeError(
            f"refusing menu action: requested pid={pid} window={window_id}, "
            f"native focused windows={focused}; activate the exact window first")


def request_path(pid: int) -> str:
    host = f"/var/mnt/rootfs/private/tmp/macws_app_input.{pid}.sock"
    return host if os.path.exists(host) else f"/private/tmp/macws_app_input.{pid}.sock"


def snapshot_path(pid: int, nonce: int) -> str:
    name = f"macws_menu_snapshot.{pid}.{nonce:016x}.bin"
    host = f"/var/mnt/rootfs/private/tmp/{name}"
    return host if os.path.exists(os.path.dirname(host)) else f"/private/tmp/{name}"


def exchange(pid: int, request: bytes) -> bytes:
    nonce = struct.unpack_from("<Q", request, 12)[0]
    source = f"/var/mnt/rootfs/private/tmp/macws_menu_probe.{os.getpid()}.{nonce:016x}.sock"
    if not os.path.exists(os.path.dirname(source)):
        source = f"/private/tmp/macws_menu_probe.{os.getpid()}.{nonce:016x}.sock"
    try:
        os.unlink(source)
    except FileNotFoundError:
        pass
    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    client.settimeout(2.0)
    try:
        client.bind(source)
        client.sendto(request, request_path(pid))
        reply = client.recv(HEADER.size)
    finally:
        client.close()
        try:
            os.unlink(source)
        except FileNotFoundError:
            pass
    if len(reply) != HEADER.size:
        raise RuntimeError(f"short menu reply: {len(reply)} bytes")
    return reply


def unpack_header(data: bytes):
    values = HEADER.unpack_from(data)
    if values[0] != MAGIC or values[1] != VERSION or values[2] != HEADER.size:
        raise RuntimeError(f"invalid menu response header: {values[:3]}")
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("window", type=int)
    parser.add_argument("--shortcut")
    parser.add_argument("--title")
    parser.add_argument("--item-id", type=int,
                        help="Disambiguate an item observed in --list (e.g. About vs Window menu).")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--perform", action="store_true")
    args = parser.parse_args()
    if args.shortcut is None and args.title is None and not args.list:
        args.shortcut = "⌘O"

    if args.perform:
        require_focused_target(args.pid, args.window)

    nonce = (time.time_ns() ^ os.getpid()) & ((1 << 64) - 1) or 1
    request = REQUEST.pack(MAGIC, VERSION, REQUEST.size, 1, 0, nonce,
                           args.pid, args.window, 0, 0)
    acknowledgement = unpack_header(exchange(args.pid, request))
    status, generation = acknowledgement[3], acknowledgement[8]
    if status != 1:
        raise RuntimeError(f"snapshot failed with status={status}")

    sidecar = snapshot_path(args.pid, nonce)
    with open(sidecar, "rb") as stream:
        payload = stream.read()
    os.unlink(sidecar)
    header = unpack_header(payload)
    node_count, string_bytes, total_bytes = header[10:13]
    if total_bytes != len(payload):
        raise RuntimeError(f"snapshot size mismatch: {total_bytes} != {len(payload)}")
    strings_offset = HEADER.size + node_count * NODE.size
    strings = payload[strings_offset:strings_offset + string_bytes]
    matches = []
    for index in range(node_count):
        values = NODE.unpack_from(payload, HEADER.size + index * NODE.size)
        item_id, _, _, flags, _, title_off, title_len, key_off, key_len = values
        title = strings[title_off:title_off + title_len].decode("utf-8")
        shortcut = strings[key_off:key_off + key_len].decode("utf-8")
        if args.list and (title or shortcut):
            print(f"item={item_id} flags={flags:#x} title={title!r} shortcut={shortcut!r}")
        selected = ((args.shortcut is not None and shortcut == args.shortcut) or
                    (args.title is not None and title == args.title))
        if (selected and (args.item_id is None or item_id == args.item_id)
                and flags & 2 and not flags & (4 | 8 | 128)):
            matches.append((item_id, title, shortcut))
    print(f"snapshot status={status} generation={generation} nodes={node_count}")
    for item_id, title, shortcut in matches:
        print(f"match item={item_id} title={title!r} shortcut={shortcut!r}")
    if not args.perform:
        return
    if args.shortcut is None and args.title is None:
        raise RuntimeError("--perform requires --shortcut or --title")
    if len(matches) != 1:
        raise RuntimeError(f"expected one actionable match, found {len(matches)}")
    require_focused_target(args.pid, args.window)
    action_nonce = (time.time_ns() ^ (os.getpid() << 1)) & ((1 << 64) - 1) or 1
    action = REQUEST.pack(MAGIC, VERSION, REQUEST.size, 2, 0, action_nonce,
                          args.pid, args.window, generation, matches[0][0])
    action_reply = unpack_header(exchange(args.pid, action))
    print(f"action-accepted status={action_reply[3]} generation={action_reply[8]} "
          "completion=UNVERIFIED")
    if action_reply[3] != 1:
        raise RuntimeError(f"action failed with status={action_reply[3]}")


if __name__ == "__main__":
    main()
