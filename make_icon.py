"""生成应用图标 assets/icon.ico：深色圆角块 + 翡翠进度环。

用法：.venv\\Scripts\\python.exe make_icon.py
"""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush, QColor, QGuiApplication, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient,
)

SIZES = [256, 128, 64, 48, 32, 16]
OUT = Path(__file__).parent / "assets" / "icon.ico"
EMERALD = QColor("#34d399")


def render(size: int) -> QPixmap:
    """按 4× 超采样绘制，再缩到目标尺寸（抗锯齿）。"""
    s = size * 4
    margin = s * 0.0625
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # 深色圆角底 + 微弱顶光
    body = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)
    path = QPainterPath()
    path.addRoundedRect(body, body.width() * 0.24, body.width() * 0.24)
    grad = QLinearGradient(body.topLeft(), body.bottomLeft())
    grad.setColorAt(0.0, QColor("#1d1d27"))
    grad.setColorAt(1.0, QColor("#111117"))
    p.fillPath(path, QBrush(grad))
    p.setPen(QPen(QColor(255, 255, 255, 36), s * 0.008))
    p.drawPath(path)

    # 进度环：浅色轨道 + 3/4 环翡翠弧
    cx = cy = s / 2
    r = body.width() * 0.30
    p.setPen(QPen(QColor(255, 255, 255, 26), s * 0.055))
    p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
    pen = QPen(EMERALD, s * 0.055)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), 90 * 16, -270 * 16)

    # 中心光晕 + 实心点
    halo = QRadialGradient(cx, cy, s * 0.16)
    halo.setColorAt(0.0, QColor(52, 211, 153, 130))
    halo.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(halo))
    p.drawEllipse(QRectF(cx - s * 0.16, cy - s * 0.16, s * 0.32, s * 0.32))
    p.setBrush(EMERALD)
    dot = s * 0.075
    p.drawEllipse(QRectF(cx - dot, cy - dot, 2 * dot, 2 * dot))
    p.end()
    return pm.scaled(QSize(size, size), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def to_png(pm: QPixmap) -> bytes:
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    pm.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(pngs: list[bytes], sizes: list[int]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack("<HHH", 0, 1, len(pngs))  # reserved, type=icon, count
    entries = b""
    blobs = b""
    offset = 6 + 16 * len(pngs)
    for png, sz in zip(pngs, sizes):
        w = 0 if sz == 256 else sz
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    OUT.write_bytes(header + entries + blobs)


if __name__ == "__main__":
    app = QGuiApplication([])
    pms = [render(s) for s in SIZES]
    write_ico([to_png(pm) for pm in pms], SIZES)
    print(f"written {OUT} ({OUT.stat().st_size} bytes)")
