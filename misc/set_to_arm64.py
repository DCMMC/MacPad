import sys
with open(sys.argv[1], "r+b") as infile:
  infile.seek(4)
  infile.write(b"\x0c\x00\x00\x01\x00\x00\x00\x00")

