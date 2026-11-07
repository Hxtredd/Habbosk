"""Thin, fast wrapper around the local Tesseract executable.

Images are piped through stdin/stdout (no temp files) and every call runs in
its own process, so several cards can be recognised in parallel threads.
Nothing is sent over the network.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from .config import Config, app_dir


@dataclass
class Word:
    text: str
    conf: float
    x: int
    y: int
    w: int
    h: int
    line: int  # block*10000 + par*100 + line, groups words on one text line

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


class OcrError(RuntimeError):
    pass


def find_tesseract(cfg: Config) -> Optional[str]:
    candidates = []
    if cfg.tesseract_cmd:
        candidates.append(cfg.tesseract_cmd)
    local = os.path.join(app_dir(), "Tesseract-OCR", "tesseract.exe")
    candidates.append(local)
    which = shutil.which("tesseract")
    if which:
        candidates.append(which)
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        candidates.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


class Tesseract:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cmd = find_tesseract(cfg)
        if not self.cmd:
            raise OcrError(
                "Tesseract OCR was not found. Run INSTALL.bat or install it from "
                "https://github.com/UB-Mannheim/tesseract/wiki and set "
                "'tesseract_cmd' in config.json.")
        tessdata = cfg.tessdata_dir or os.path.join(app_dir(), "tessdata")
        self.tessdata = tessdata if os.path.isfile(
            os.path.join(tessdata, cfg.language + ".traineddata")) else None
        self.env = dict(os.environ)
        # one thread per process; we parallelise across processes instead
        self.env["OMP_THREAD_LIMIT"] = "1"
        self.creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        self._use_files = False

    def _run(self, img: np.ndarray, psm: int, extra: List[str], fmt: str) -> str:
        ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not ok:
            raise OcrError("could not encode image")
        args = [self.cmd, "stdin", "stdout", "--psm", str(psm), "--oem", "1",
                "-l", self.cfg.language]
        if self.tessdata:
            args[1:1] = ["--tessdata-dir", self.tessdata]
        # pass output options as -c variables: config files like "tsv" are not
        # present in a bundled tessdata folder
        if fmt == "tsv":
            args += ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
        args += extra
        if self._use_files:
            return self._run_via_file(args, buf)
        try:
            proc = subprocess.run(args, input=buf.tobytes(), capture_output=True,
                                  env=self.env, timeout=10,
                                  creationflags=self.creationflags)
        except subprocess.TimeoutExpired as exc:
            raise OcrError("Tesseract timed out") from exc
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")
            if "pixRead" in err or "stdin" in err.lower():
                # very old builds cannot read images from stdin: use temp files
                self._use_files = True
                return self._run_via_file(args, buf)
            raise OcrError(err.strip()[:300])
        return proc.stdout.decode("utf-8", "replace")

    def _run_via_file(self, args: List[str], buf) -> str:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(buf.tobytes())
            a = list(args)
            a[a.index("stdin")] = path
            proc = subprocess.run(a, capture_output=True, env=self.env, timeout=10,
                                  creationflags=self.creationflags)
            if proc.returncode != 0:
                raise OcrError(proc.stderr.decode("utf-8", "replace").strip()[:300])
            return proc.stdout.decode("utf-8", "replace")
        except subprocess.TimeoutExpired as exc:
            raise OcrError("Tesseract timed out") from exc
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def words(self, img: np.ndarray, psm: int = 6, extra: Optional[List[str]] = None) -> List[Word]:
        tsv = self._run(img, psm, list(extra or []), "tsv")
        out: List[Word] = []
        for row in tsv.splitlines()[1:]:
            cols = row.split("\t")
            if len(cols) < 12 or cols[0] != "5":
                continue
            text = cols[11].strip()
            if not text:
                continue
            try:
                conf = float(cols[10])
                x, y, w, h = (int(c) for c in cols[6:10])
                line = int(cols[2]) * 10000 + int(cols[3]) * 100 + int(cols[4])
            except ValueError:
                continue
            out.append(Word(text, conf, x, y, w, h, line))
        return out

    def text(self, img: np.ndarray, psm: int = 6) -> str:
        return self._run(img, psm, [], "txt")
