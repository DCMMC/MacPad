"""Read one AppKit menu snapshot through the production app-input protocol.

Run on the iPad's iOS side with a PID and CGWindow ID.  This probe sends no
menu actions and removes only the socket/sidecar files it creates.
"""

import argparse
import os
import socket
import struct
import time


ROOT = "/var/mnt/rootfs/private/tmp"
REQUEST = struct.Struct("<IHHHHQiIQQ")
HEADER = struct.Struct("<IHHHHQiIQIIII")
NODE = struct.Struct("<QQIIiIIII")
MAGIC = 0x4D4E574D


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("window_id", type=int)
    args = parser.parse_args()
    nonce = time.monotonic_ns() & ((1 << 64) - 1)
    local = f"{ROOT}/macws_menu_probe.{os.getpid()}.sock"
    sidecar = f"{ROOT}/macws_menu_snapshot.{args.pid}.{nonce:016x}.bin"
    peer = f"{ROOT}/macws_app_input.{args.pid}.sock"
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    connection.settimeout(3)
    try:
        connection.bind(local)
        request = REQUEST.pack(MAGIC, 2, REQUEST.size, 1, 0,
                               nonce, args.pid, args.window_id, 0, 0)
        connection.sendto(request, peer)
        acknowledgement = connection.recv(HEADER.size)
        status = HEADER.unpack(acknowledgement)[3]
        print(f"ack status={status} bytes={len(acknowledgement)}")
        if status != 1:
            return
        with open(sidecar, "rb") as source:
            data = source.read()
        header = HEADER.unpack_from(data)
        count, string_bytes = header[10], header[11]
        strings_offset = HEADER.size + count * NODE.size
        print(f"snapshot nodes={count} strings={string_bytes}")
        for index in range(count):
            node = NODE.unpack_from(data, HEADER.size + index * NODE.size)
            item_id, parent, sibling, flags = node[:4]
            title_offset, title_size = node[5:7]
            title = data[strings_offset + title_offset:
                         strings_offset + title_offset + title_size].decode(
                             "utf-8", "replace")
            if parent == 0:
                children = sum(NODE.unpack_from(data,
                    HEADER.size + child * NODE.size)[1] == item_id
                    for child in range(count))
                print(f"root[{sibling}] title={title!r} flags={flags:#x} "
                      f"children={children}")
    finally:
        connection.close()
        for path in (local, sidecar):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
