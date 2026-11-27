#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The `ioc-concord` command. It is the library's: `cli_run` does everything (the arguments, the output, the state it keeps); this
passes it the arguments and puts what comes back on the screen."""
import os
import signal
import sys

from . import cli_run


def _write(stream, text):
    # bytes, in UTF-8: the text of a message is read as UTF-8, and a Windows console or pipe would encode it in its code page (and make `\n` `\r\n`)
    stream.flush()
    stream.buffer.write(text.encode("utf-8"))
    stream.buffer.flush()


def main():
    # the state is saved when `cli_run` returns: an interrupt must not lose the output that goes with it
    interrupted = []
    previous = signal.signal(signal.SIGINT, lambda *_: interrupted.append(True))
    try:
        output = cli_run(sys.argv[1:])
    finally:
        signal.signal(signal.SIGINT, previous)
    code = output.code
    try:
        _write(sys.stdout, output.stdout)
        _write(sys.stderr, output.stderr)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())  # the reader has gone: nothing is left to say
        code = 141
    sys.exit(130 if interrupted and code == 0 else code)


if __name__ == "__main__":
    main()
