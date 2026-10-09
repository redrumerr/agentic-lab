"""Drain both pipes concurrently; deliver output before the command exits."""

import codecs
import os
import signal
import subprocess
import threading

from .models import ExecResult


def collect(process, timeout, emit):
    """Читает stdout/stderr одновременно и доставляет блоки в журнал задачи.

    Два потока не дают одному заполненному pipe заблокировать другой. UTF-8 decoder
    сохраняет неполный символ между блоками. В итоговый ExecResult попадает только
    ограниченный объём вывода; поток событий ограничивает сама очередь Jobs.
    После timeout или завершения основного процесса убиваем его группу: дочерний
    процесс иначе мог бы держать pipe открытым и не дать завершить reader threads.
    """
    buffers = {"stdout": bytearray(), "stderr": bytearray()}

    def drain(pipe, name):
        """Читает один pipe, сохраняет ограниченный результат и отправляет декодированные события."""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while chunk := pipe.read1(4096):
                available = max(0, 524288 - len(buffers[name]))
                buffers[name].extend(chunk[:available])
                emit(name, decoder.decode(chunk))
            tail = decoder.decode(b"", final=True)
            if tail:
                emit(name, tail)
        finally:
            pipe.close()

    readers = [
        threading.Thread(target=drain, args=(getattr(process, name), name), daemon=True)
        for name in buffers
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    finally:
        # Reap pipe-holding descendants too, even if the main process exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for reader in readers:
            reader.join()
    return ExecResult(
        exit_code=124 if timed_out else process.returncode,
        stdout=buffers["stdout"].decode(errors="replace"),
        stderr=buffers["stderr"].decode(errors="replace"),
        timed_out=timed_out,
    )
