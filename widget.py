"""PySide6 悬浮条：折叠为一条小字，悬停展开双口径预估面板。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import QEasingCurve, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QVBoxLayout, QWidget,
)

from config import (
    COLOR_AMBER, COLOR_GREEN, COLOR_RED, COLLAPSED_H, EXPAND_MS,
    PANEL_H, PANEL_W, USAGE_PAGE,
)
from forecast import Forecast, forecast
from history import append_sample, load_samples, load_settings, save_settings
from usage import UsageSnapshot

SHELL_QSS = """
#shell { background: rgba(20,20,26,0.86); border-radius: 10px;
         border: 1px solid rgba(255,255,255,0.10); }
#bar { color: #e8e8ec; font-size: 12px; }
#pct { font-size: 24px; font-weight: 700; }
#countdown { color: #9a9aa4; font-size: 11px; }
#muted { color: #9a9aa4; font-size: 11px; }
#card { background: rgba(255,255,255,0.07); border-radius: 8px; }
#card_title { color: #8f8f9a; font-size: 10px; }
#card_main { color: #e8e8ec; font-size: 13px; font-weight: 600; }
#card_sub { color: #9a9aa4; font-size: 11px; }
#bar_progress { background: rgba(255,255,255,0.12); border: none;
                border-radius: 2px; max-height: 4px; min-height: 4px; }
QMenu { background: #1c1c22; color: #e8e8ec; border: 1px solid #33333c;
        border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: #2e2e38; }
"""


def _status_color(remain_pct: float) -> str:
    if remain_pct > 40:
        return COLOR_GREEN
    if remain_pct > 15:
        return COLOR_AMBER
    return COLOR_RED


def _fmt_countdown(resets_ts) -> str:
    if not resets_ts:
        return "重置时间未知"
    secs = max(0, int((datetime.fromtimestamp(resets_ts) - datetime.now()).total_seconds()))
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    return f"距重置 {d}天{h}时" if d else f"距重置 {h}时{r // 60}分"


class QuotaWidget(QWidget):
    updated = Signal(object)  # submit → emit → 队列投递到 UI 线程

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._snapshot: Optional[UsageSnapshot] = None
        self._forecast: Optional[Forecast] = None
        self._expanded = False
        self._press_pos = None
        self._dragging = False
        self.refresh_callback = None  # main 注入：立即刷新
        self._build_ui()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(EXPAND_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_size_anim)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(250)
        self._collapse_timer.timeout.connect(self._try_collapse)
        self.updated.connect(self._on_snapshot)
        self._restore_pos()
        self._apply_state()

    # ---- UI 构建 ----
    def _build_ui(self) -> None:
        shell = QFrame(self, objectName="shell")
        shell.setStyleSheet(SHELL_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        lay = QVBoxLayout(shell)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(6)

        self.bar = QLabel("加载中…", objectName="bar")
        lay.addWidget(self.bar)

        self.panel = QFrame(objectName="panel")
        lay.addWidget(self.panel)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(2, 2, 2, 2)
        pl.setSpacing(6)

        head = QHBoxLayout()
        self.pct = QLabel("--", objectName="pct")
        self.countdown = QLabel("", objectName="countdown")
        head.addWidget(self.pct)
        head.addStretch(1)
        head.addWidget(self.countdown, 0, Qt.AlignTop)
        pl.addLayout(head)

        self.progress = QProgressBar(objectName="bar_progress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        pl.addWidget(self.progress)

        cards = QHBoxLayout()
        cards.setSpacing(6)
        self.wd_card = self._make_card("工作日节奏 · 周一~五")
        self.ed_card = self._make_card("每天都在用 · 含周末")
        cards.addWidget(self.wd_card)
        cards.addWidget(self.ed_card)
        pl.addLayout(cards)

        self.extra = QLabel("", objectName="muted")
        self.footer = QLabel("", objectName="muted")
        pl.addWidget(self.extra)
        pl.addWidget(self.footer)

        self.panel.hide()
        self.setGeometry(self.x(), self.y(), self._bar_width(), COLLAPSED_H)

    def _make_card(self, title: str) -> QFrame:
        card = QFrame(objectName="card")
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addWidget(QLabel(title, objectName="card_title"))
        card.main_label = QLabel("--", objectName="card_main")
        card.sub_label = QLabel("", objectName="card_sub")
        v.addWidget(card.main_label)
        v.addWidget(card.sub_label)
        return card

    def _bar_width(self) -> int:
        return max(96, self.bar.sizeHint().width() + 28)

    # ---- 位置与生命周期 ----
    def _restore_pos(self) -> None:
        pos = load_settings().get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            g = QApplication.primaryScreen().availableGeometry()
            self.move(g.right() - 150, g.bottom() - 80)

    def closeEvent(self, event) -> None:
        save_settings({"pos": [self.x(), self.y()]})
        super().closeEvent(event)

    # ---- 展开 / 收起 ----
    def enterEvent(self, event) -> None:
        self._expand()

    def leaveEvent(self, event) -> None:
        self._collapse_timer.start()

    def _expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self._collapse_timer.stop()
        self.panel.show()
        self._start_anim(1.0)
        self.raise_()

    def _try_collapse(self) -> None:
        if self.underMouse() or not self._expanded:
            return
        self._expanded = False
        self.panel.hide()
        self._start_anim(0.0)

    def _start_anim(self, end: float) -> None:
        geo = self.geometry()
        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.left(), min(geo.x(), screen.right() - PANEL_W + 1))
        self._anchor = (x, geo.y())
        self._anim.stop()
        self._anim.setStartValue(1.0 - end)
        self._anim.setEndValue(end)
        self._anim.start()

    def _on_size_anim(self, v) -> None:
        p = float(v)
        wc = self._bar_width()
        w = int(wc + (PANEL_W - wc) * p)
        h = int(COLLAPSED_H + (PANEL_H - COLLAPSED_H) * p)
        self.setGeometry(self._anchor[0], self._anchor[1], w, h)

    # ---- 拖动 / 点击 / 菜单 ----
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press_pos = (e.globalPosition().toPoint(), self.pos())
            self._dragging = False

    def mouseMoveEvent(self, e) -> None:
        if self._press_pos is None:
            return
        gpos, origin = self._press_pos
        delta = e.globalPosition().toPoint() - gpos
        if self._dragging or delta.manhattanLength() > 4:
            self._dragging = True
            self._anim.stop()
            self.move(origin + delta)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and not self._dragging:
            QDesktopServices.openUrl(QUrl(USAGE_PAGE))
        self._press_pos = None
        self._dragging = False

    def contextMenuEvent(self, e) -> None:
        m = QMenu(self)
        m.setStyleSheet(SHELL_QSS)
        if self.refresh_callback:
            m.addAction("立即刷新", self.refresh_callback)
        m.addAction("打开用量页", lambda: QDesktopServices.openUrl(QUrl(USAGE_PAGE)))
        m.addSeparator()
        m.addAction("退出", QApplication.quit)
        m.exec(e.globalPos())

    # ---- 数据 ----
    def submit(self, snapshot: UsageSnapshot) -> None:
        self.updated.emit(snapshot)  # 任意线程可调用

    def _on_snapshot(self, snap: UsageSnapshot) -> None:
        if snap.weekly is not None:
            append_sample(snap)
            self._snapshot = snap
        elif self._snapshot is None:
            self._snapshot = snap
        else:  # 拉取失败：保留上次数据，带上错误标记
            self._snapshot = UsageSnapshot(
                fetched_at=self._snapshot.fetched_at,
                weekly=self._snapshot.weekly,
                five_hour=self._snapshot.five_hour,
                error=snap.error,
            )
        self._apply_state()

    def _set_bar(self, text: str, color: str) -> None:
        self.bar.setText(text)
        self.bar.setStyleSheet(f"color: {color};")

    def _apply_state(self) -> None:
        snap = self._snapshot
        if snap is None:
            self._set_bar("加载中…", "#8a8a94")
            return
        if snap.weekly is None:
            self._set_bar("● 未登录", COLOR_RED)
            self.footer.setText("未登录：请先用 Codex CLI 登录")
            return

        w = snap.weekly
        remain = 100.0 - w.used_pct
        color = "#8a8a94" if snap.error else _status_color(remain)
        err = " ·未更新" if snap.error else ""
        if w.resets_at:
            days = max((datetime.fromtimestamp(w.resets_at) - datetime.now()).total_seconds() / 86400, 0.0)
            self._set_bar(f"● {remain:.0f}%  {days:.1f}d{err}", color)
        else:
            self._set_bar(f"● {remain:.0f}%{err}", color)

        self.pct.setText(f"{remain:.0f}%")
        self.pct.setStyleSheet(f"color: {color};")
        self.progress.setValue(int(remain))
        self.progress.setStyleSheet(
            f"#bar_progress::chunk {{ background: {color}; border-radius: 2px; }}")
        self.countdown.setText(_fmt_countdown(w.resets_at))

        self._forecast = None
        if w.resets_at:
            self._forecast = forecast(
                w.used_pct,
                datetime.fromtimestamp(w.resets_at),
                w.window_minutes,
                history_samples=load_samples(),
            )
        if self._forecast:
            f = self._forecast
            self._fill_card(self.wd_card, f.working_day, f.remaining_wd_h, "个工作日",
                            "" if f.fallback_wd else "按历史速率")
            self._fill_card(self.ed_card, f.every_day, f.remaining_all_h, "天", "")
        extra = (f"5h 窗口剩余 {100 - snap.five_hour.used_pct:.0f}%"
                 if snap.five_hour else "")
        self.extra.setText(extra)
        self.extra.setVisible(bool(extra))
        ts = datetime.fromtimestamp(snap.fetched_at).strftime("%H:%M")
        self.footer.setText(f"更新 {ts}" + (f" · {snap.error}" if snap.error else ""))
        if not self._expanded:
            self.setGeometry(self.x(), self.y(), self._bar_width(), COLLAPSED_H)

    def _fill_card(self, card, sc, remaining_h: float, unit: str, tag: str) -> None:
        if sc.end_remaining_pct >= 0:
            card.main_label.setText(f"结余 {sc.end_remaining_pct:.0f}%")
            card.main_label.setStyleSheet(f"color: {COLOR_GREEN};")
        else:
            ahead = max(0.0, remaining_h - sc.hours_left)
            text = f"超 {-sc.end_remaining_pct:.0f}%"
            if ahead > 0:
                text += f" ·提前{ahead:.0f}h用完"
            card.main_label.setText(text)
            card.main_label.setStyleSheet(f"color: {COLOR_RED};")
        sub = f"还能撑 {sc.days_left:.1f} {unit}"
        if tag:
            sub += f" · {tag}"
        card.sub_label.setText(sub)
