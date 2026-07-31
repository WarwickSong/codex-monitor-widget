"""Glanceable ambient PySide6 widget for Codex health and quota.

Design principles:
- Visual encoding: color + shape + glow convey status without reading text
- Progressive disclosure: chart collapses to sparkline
- Ambient display: pulse on active monitoring, alert pulse on danger
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QLockFile,
    QPointF,
    QRectF,
    QRunnable,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from monitor import (
    Rate,
    Sample,
    calc_rate,
    ensure_protection_trigger,
    install_protection_trigger,
    take_sample,
)
from usage import UsageInfo, format_reset_time, get_usage, open_usage_page

COLORS = {
    "canvas": "#0b0e12",
    "panel": "#12171d",
    "surface": "#181e25",
    "surface_alt": "#1d242c",
    "border": "#29313a",
    "text": "#eef2f5",
    "muted": "#89949f",
    "faint": "#5f6973",
    "green": "#43d17d",
    "green_bg": "#123626",
    "blue": "#5fa8ff",
    "amber": "#f4bd4f",
    "amber_bg": "#3b2d12",
    "red": "#ff6b6b",
    "red_bg": "#411d20",
}
MIN_WIDGET_WIDTH = 220
MAX_WIDGET_WIDTH = 480
MIN_WIDGET_HEIGHT = 58
MAX_WIDGET_HEIGHT = 720
RESIZE_MARGIN = 6
_instance_lock: QLockFile | None = None


def _font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont("Segoe UI", size)
    font.setWeight(weight)
    return font


# ── Background workers ──────────────────────────────────────────────────

class WorkerSignals(QObject):
    finished = Signal(object)


class UsageWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(get_usage())
        except RuntimeError:
            pass


# ── Reusable UI atoms ───────────────────────────────────────────────────

class IconButton(QPushButton):
    def __init__(self, icon, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self.setIcon(icon)
        self.setToolTip(tooltip)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton {{ border: 0; border-radius: 5px; background: transparent; }}"
            f"QPushButton:hover {{ background: {COLORS['surface_alt']}; }}"
            f"QPushButton:pressed {{ background: {COLORS['border']}; }}"
        )


class StatusDot(QWidget):
    """Pulsing circle — green pulse = running, gray static = stopped, red pulse = danger."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = QColor(COLORS["faint"])
        self._phase = 0.0
        self._pulsing = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase += 0.06
        self.update()

    def set_state(self, color: str, pulsing: bool) -> None:
        self._color = QColor(color)
        self._pulsing = pulsing
        if pulsing:
            self._timer.start(50)
        else:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 8.0, 8.0
        if self._pulsing:
            pulse = (math.sin(self._phase) + 1) / 2  # 0..1
            glow = QColor(self._color)
            glow.setAlpha(int(50 + pulse * 90))
            p.setBrush(glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 4 + pulse * 4, 4 + pulse * 4)
        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 3.2, 3.2)


class MeaningPopover(QWidget):
    """Small non-activating legend used to explain ambient visual encoding."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("meaningCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)
        self.title = QLabel("守护环")
        self.title.setFont(_font(9, QFont.Weight.DemiBold))
        self.body = QLabel()
        self.body.setFont(_font(7))
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setWordWrap(True)
        self.body.setFixedWidth(218)
        card_layout.addWidget(self.title)
        card_layout.addWidget(self.body)
        layout.addWidget(card)
        self.setStyleSheet(
            f"QFrame#meaningCard {{ background: #20262e; border: 1px solid #38424d; border-radius: 10px; }}"
            f"QLabel {{ color: {COLORS['text']}; background: transparent; }}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(0, 0, 0, 165))
        card.setGraphicsEffect(shadow)

    def set_content(self, html: str) -> None:
        self.body.setText(html)
        self.adjustSize()


class GuardianOrb(QWidget):
    """One ambient object: core health, motion activity, outer-ring quota."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(42, 42)
        self._color = QColor(COLORS["faint"])
        self._quota: float | None = None
        self._phase = 0.0
        self._pulsing = False
        self._state_name = "正在检查"
        self._popover = MeaningPopover()
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._show_popover)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase += 0.055
        self.update()

    def set_state(self, color: str, pulsing: bool, state_name: str | None = None) -> None:
        self._color = QColor(color)
        self._pulsing = pulsing
        if state_name:
            self._state_name = state_name
        if pulsing:
            self._timer.start(50)
        else:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def enterEvent(self, event) -> None:
        self._show_timer.start(260)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._show_timer.stop()
        self._popover.hide()
        super().leaveEvent(event)

    def _show_popover(self) -> None:
        quota_text = "尚未取得额度" if self._quota is None else f"剩余 {self._quota:.0f}%"
        motion_text = "正在呼吸，表示需要注意" if self._pulsing else "保持静止，表示当前平稳"
        color = self._color.name()
        self._popover.set_content(
            f"<div style='line-height:1.45'>"
            f"<span style='color:{color}'>●</span>&nbsp; <b>核心</b>　{self._state_name}<br>"
            f"<span style='color:#eef2f5'>◯</span>&nbsp; <b>外环</b>　{quota_text}<br>"
            f"<span style='color:#89949f'>≈</span>&nbsp; <b>动态</b>　{motion_text}"
            f"</div>"
        )
        self._popover.adjustSize()
        anchor = self.mapToGlobal(self.rect().bottomLeft())
        x, y = anchor.x(), anchor.y() + 8
        screen = QApplication.screenAt(anchor)
        if screen is not None:
            area = screen.availableGeometry()
            x = min(x, area.right() - self._popover.width())
            if y + self._popover.height() > area.bottom():
                y = self.mapToGlobal(self.rect().topLeft()).y() - self._popover.height() - 8
        self._popover.move(max(0, x), max(0, y))
        self._popover.show()

    def set_quota(self, percent: float | None) -> None:
        self._quota = None if percent is None else max(0.0, min(100.0, percent))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = float(min(self.width(), self.height()))
        center = QPointF(self.width() / 2, self.height() / 2)
        scale = size / 42.0
        pulse = (math.sin(self._phase) + 1) / 2 if self._pulsing else 0.0

        glow = QColor(self._color)
        glow.setAlpha(int(22 + pulse * 32))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(center, (12 + pulse * 2.5) * scale, (12 + pulse * 2.5) * scale)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(COLORS["border"]), max(1.4, 2.0 * scale)))
        inset = 4 * scale
        ring = QRectF(
            center.x() - size / 2 + inset,
            center.y() - size / 2 + inset,
            size - inset * 2,
            size - inset * 2,
        )
        p.drawEllipse(ring)
        if self._quota is not None:
            quota_color = (
                COLORS["red"] if self._quota <= 10
                else COLORS["amber"] if self._quota <= 25
                else COLORS["text"]
            )
            p.setPen(QPen(QColor(quota_color), max(1.5, 2.2 * scale), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, int(-360 * 16 * self._quota / 100))

        core = QColor(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(core)
        p.drawEllipse(center, 5.2 * scale, 5.2 * scale)
        highlight = QColor(255, 255, 255, 95)
        p.setBrush(highlight)
        p.drawEllipse(
            QPointF(center.x() - 1.6 * scale, center.y() - 1.8 * scale),
            1.2 * scale,
            1.2 * scale,
        )


class ShieldIcon(QWidget):
    """Diamond — green glow = protected, red pulse = exposed."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = QColor(COLORS["faint"])
        self._active = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase += 0.08
        self.update()

    def set_state(self, active: bool) -> None:
        self._active = active
        self._color = QColor(COLORS["green"] if active else COLORS["red"])
        if active:
            self._timer.stop()
        else:
            self._timer.start(50)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 8.0, 8.0
        if self._active:
            # Subtle steady glow
            glow = QColor(self._color)
            glow.setAlpha(55)
            p.setBrush(glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 7, 7)
        else:
            # Alert pulse
            pulse = (math.sin(self._phase) + 1) / 2
            glow = QColor(self._color)
            glow.setAlpha(int(40 + pulse * 80))
            p.setBrush(glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 5 + pulse * 3, 5 + pulse * 3)
        # Diamond shape
        path = QPainterPath()
        path.moveTo(cx, cy - 5)
        path.lineTo(cx + 4, cy)
        path.lineTo(cx, cy + 5)
        path.lineTo(cx - 4, cy)
        path.closeSubpath()
        p.fillPath(path, self._color)


class Panel(QFrame):
    """Panel with a colored accent bar at the top edge."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self._accent = COLORS["faint"]

    def set_accent(self, color: str) -> None:
        self._accent = color
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)


# ── Quota ────────────────────────────────────────────────────────────────

class QuotaBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.name = QLabel("额度窗口")
        self.name.setFont(_font(8, QFont.Weight.DemiBold))
        self.percent = QLabel("--")
        self.percent.setFont(_font(8, QFont.Weight.DemiBold))
        header.addWidget(self.name)
        header.addStretch()
        header.addWidget(self.percent)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.reset = QLabel("等待刷新")
        self.reset.setFont(_font(7))
        self.reset.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addLayout(header)
        layout.addWidget(self.progress)
        layout.addWidget(self.reset)

    def set_window(self, label: str, remaining: float, reset_text: str) -> None:
        value = max(0, min(100, round(remaining)))
        color = COLORS["green"] if value >= 35 else COLORS["amber"] if value >= 15 else COLORS["red"]
        self.name.setText(label)
        self.percent.setText(f"剩余 {value}%")
        self.percent.setStyleSheet(f"color: {color};")
        self.progress.setValue(value)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: {COLORS['border']}; border: 0; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}"
        )
        self.reset.setText(reset_text)


# ── Main widget ──────────────────────────────────────────────────────────

class FloatingWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Codex Monitor")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(MIN_WIDGET_WIDTH, MIN_WIDGET_HEIGHT)
        self.setMaximumSize(MAX_WIDGET_WIDTH, MAX_WIDGET_HEIGHT)
        self.resize(320, 360)
        self.setMouseTracking(True)
        self._drag_offset = None
        self._resize_edges = Qt.Edge(0)
        self._expanded_size = None
        self._last_sample: Sample | None = None
        self._last_trigger_hash: str = ""
        self._usage_loading = False
        self._quota_percent: float | None = None
        self._collapsed = False
        self._thread_pool = QThreadPool.globalInstance()
        self._settings = QSettings("CodexMonitor", "Widget")
        self._build_ui_v4()
        self._build_tray()
        self._restore_position()

        sample = take_sample()
        if sample.database_available and not sample.trigger_intact:
            install_protection_trigger()
            sample = take_sample()
        self._last_sample = sample
        if sample.trigger_hash:
            self._last_trigger_hash = sample.trigger_hash

        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.tick)
        self.monitor_timer.start(1000)
        self.usage_timer = QTimer(self)
        self.usage_timer.timeout.connect(self.refresh_usage)
        self.usage_timer.start(5 * 60 * 1000)
        self.tick()
        self.refresh_usage()

    def _build_ui_v4(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self.panel = Panel()
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.panel.setGraphicsEffect(shadow)
        root.addWidget(self.panel)

        layout = QVBoxLayout(self.panel)
        self.panel_layout = layout
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(9)
        self.status_dot = GuardianOrb()
        header.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)

        state_stack = QVBoxLayout()
        state_stack.setSpacing(0)
        self.state_label = QLabel("正在检查")
        self.state_label.setFont(_font(11, QFont.Weight.DemiBold))
        self.context_label = QLabel("读取保护状态")
        self.context_label.setFont(_font(7))
        self.context_label.setStyleSheet(f"color: {COLORS['muted']};")
        state_stack.addWidget(self.state_label)
        state_stack.addWidget(self.context_label)
        header.addLayout(state_stack)
        header.addStretch()

        self.compact_quota = QLabel("额度 --")
        self.compact_quota.setFont(_font(8, QFont.Weight.DemiBold))
        self.compact_quota.setStyleSheet(f"color: {COLORS['muted']};")
        header.addWidget(self.compact_quota)

        self.refresh_button = IconButton(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload), "刷新额度", self
        )
        self.refresh_button.clicked.connect(self.refresh_usage)
        self.collapse_button = IconButton(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarShadeButton), "折叠", self
        )
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        self.close_button = IconButton(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton), "退出", self
        )
        self.close_button.clicked.connect(self._exit_application)
        header.addWidget(self.refresh_button)
        header.addWidget(self.collapse_button)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(11)

        activity = QHBoxLayout()
        activity.setSpacing(8)
        activity_title = QLabel("当前活动")
        activity_title.setFont(_font(7))
        activity_title.setStyleSheet(f"color: {COLORS['faint']};")
        self.activity_label = QLabel("安静")
        self.activity_label.setFont(_font(9, QFont.Weight.DemiBold))
        self.activity_label.setStyleSheet(f"color: {COLORS['muted']};")
        self.rate_label = QLabel("")
        self.rate_label.setFont(_font(9, QFont.Weight.DemiBold))
        activity.addWidget(activity_title)
        activity.addWidget(self.activity_label)
        activity.addStretch()
        activity.addWidget(self.rate_label)
        body.addLayout(activity)

        self.stats_label = QLabel("等待第一份诊断样本")
        self.stats_label.setFont(_font(7))
        self.stats_label.setStyleSheet(f"color: {COLORS['faint']};")
        body.addWidget(self.stats_label)

        self.proof_label = QLabel("保护校验：待执行")
        self.proof_label.setFont(_font(7))
        self.proof_label.setStyleSheet(f"color: {COLORS['faint']};")
        self.proof_label.setWordWrap(True)
        body.addWidget(self.proof_label)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {COLORS['border']}; border: 0;")
        body.addWidget(divider)

        quota_header = QHBoxLayout()
        quota_title = QLabel("可用额度")
        quota_title.setFont(_font(8, QFont.Weight.DemiBold))
        self.plan_badge = QLabel("读取中")
        self.plan_badge.setFont(_font(7, QFont.Weight.DemiBold))
        self.plan_badge.setStyleSheet(f"color: {COLORS['faint']};")
        quota_header.addWidget(quota_title)
        quota_header.addStretch()
        quota_header.addWidget(self.plan_badge)
        body.addLayout(quota_header)

        self.quota_bars = [QuotaBar(), QuotaBar()]
        for bar in self.quota_bars:
            body.addWidget(bar)
            bar.hide()
        self.quota_fallback = QLabel("正在获取额度")
        self.quota_fallback.setFont(_font(8))
        self.quota_fallback.setStyleSheet(f"color: {COLORS['muted']};")
        body.addWidget(self.quota_fallback)

        footer = QHBoxLayout()
        self.local_usage = QLabel("本机累计 --")
        self.local_usage.setFont(_font(7))
        self.local_usage.setStyleSheet(f"color: {COLORS['faint']};")
        open_button = QPushButton("用量页  ↗")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.setFont(_font(7, QFont.Weight.DemiBold))
        open_button.clicked.connect(open_usage_page)
        open_button.setStyleSheet(
            f"QPushButton {{ border: 0; color: {COLORS['blue']}; background: transparent; }}"
            f"QPushButton:hover {{ color: {COLORS['text']}; }}"
        )
        footer.addWidget(self.local_usage)
        footer.addStretch()
        footer.addWidget(open_button)
        body.addLayout(footer)
        layout.addWidget(self.body)

        self.setStyleSheet(
            f"QWidget {{ color: {COLORS['text']}; background: transparent; }}"
            f"QFrame#panel {{ background: {COLORS['panel']}; border: 1px solid {COLORS['border']}; border-radius: 12px; }}"
            "QToolTip { background: #202730; color: #eef2f5; border: 1px solid #36404b; padding: 3px; }"
        )

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self
        )
        self.tray.setToolTip("Codex Monitor")
        menu = QMenu()
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self.restore_window)
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self._exit_application)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_window()

    def restore_window(self) -> None:
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._expanded_size = self.size()
        self.body.setVisible(not self._collapsed)
        self.context_label.setVisible(not self._collapsed)
        self.refresh_button.setVisible(not self._collapsed)
        self.close_button.setVisible(not self._collapsed)
        if self._collapsed:
            self.panel_layout.setContentsMargins(12, 6, 10, 6)
            self.panel_layout.setSpacing(0)
            self.status_dot.setFixedSize(30, 30)
            self.state_label.setFont(_font(9, QFont.Weight.DemiBold))
        else:
            self.panel_layout.setContentsMargins(16, 14, 16, 14)
            self.panel_layout.setSpacing(12)
            self.status_dot.setFixedSize(42, 42)
            self.state_label.setFont(_font(11, QFont.Weight.DemiBold))
        self._render_quota_summary()
        icon = (
            QStyle.StandardPixmap.SP_TitleBarUnshadeButton
            if self._collapsed
            else QStyle.StandardPixmap.SP_TitleBarShadeButton
        )
        self.collapse_button.setIcon(self.style().standardIcon(icon))
        self.collapse_button.setToolTip("展开" if self._collapsed else "折叠")
        if self._collapsed:
            QTimer.singleShot(0, self._fit_collapsed)
        elif self._expanded_size is not None:
            QTimer.singleShot(0, lambda: self.resize(self._expanded_size))

    def _fit_collapsed(self) -> None:
        self.resize(240, 62)

    def _render_quota_summary(self) -> None:
        if self._quota_percent is None:
            self.compact_quota.setText("--")
        elif self._collapsed:
            self.compact_quota.setText(f"{self._quota_percent:.0f}%")
        else:
            self.compact_quota.setText(f"额度 {self._quota_percent:.0f}%")

    def _restore_position(self) -> None:
        saved_size = self._settings.value("size_v4")
        if saved_size is not None:
            self.resize(saved_size)
        position = self._settings.value("position")
        screen = QApplication.primaryScreen().availableGeometry()
        if position is not None:
            self.move(position)
            geo = self.frameGeometry()
            if geo.right() < screen.left() + 100 or geo.left() > screen.right() - 100:
                self.move(screen.right() - self.width() - 18, screen.top() + 18)
        else:
            self.move(screen.right() - self.width() - 18, screen.top() + 18)

    def _save_window_state(self) -> None:
        self._settings.setValue("position", self.pos())
        self._settings.setValue("size_v4", self._expanded_size or self.size())

    def _exit_application(self) -> None:
        self._save_window_state()
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:
        self._save_window_state()
        self.tray.hide()
        event.accept()
        QTimer.singleShot(0, QApplication.instance().quit)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.position())
            if edges and self.windowHandle() is not None:
                self._resize_edges = edges
                self.windowHandle().startSystemResize(edges)
                event.accept()
                return
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        elif not event.buttons():
            self._update_resize_cursor(self._edges_at(event.position()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        self._resize_edges = Qt.Edge(0)
        event.accept()

    def leaveEvent(self, event) -> None:
        if self._drag_offset is None and not self._resize_edges:
            self.unsetCursor()
        super().leaveEvent(event)

    def _edges_at(self, position: QPointF):
        x, y = position.x(), position.y()
        edges = Qt.Edge(0)
        if x <= RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif x >= self.width() - RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if y <= RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif y >= self.height() - RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _update_resize_cursor(self, edges) -> None:
        if edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge):
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif edges in (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
            cursor = Qt.CursorShape.SizeBDiagCursor
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            cursor = Qt.CursorShape.SizeHorCursor
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            cursor = Qt.CursorShape.SizeVerCursor
        else:
            self.unsetCursor()
            return
        self.setCursor(cursor)

    # ── Tick: update all visual indicators ──

    def tick(self) -> None:
        current = take_sample()
        rate = calc_rate(self._last_sample, current) if self._last_sample else Rate()
        self._last_sample = current

        if current.database_available and not current.trigger_intact:
            result = ensure_protection_trigger(current)
            if result.success:
                current = take_sample()
                self._last_sample = current

        if current.database_available:
            self.stats_label.setText(
                f"{current.rows:,} 条事件   ·   ID {self._format_count(current.max_id)}"
                f"   ·   WAL {self._format_bytes(current.wal_bytes)}"
            )
        else:
            self.stats_label.setText("诊断数据不可用")

        if rate.row_rate > 0 or rate.wal_rate > 0:
            self.activity_label.setText("检测到写入")
            self.activity_label.setStyleSheet(f"color: {COLORS['amber']};")
            self.rate_label.setText(f"{rate.row_rate:.1f} /s")
            self.rate_label.setStyleSheet(f"color: {COLORS['amber']};")
        else:
            self.activity_label.setText("安静")
            self.activity_label.setStyleSheet(f"color: {COLORS['muted']};")
            self.rate_label.clear()

        # 检测触发器 SQL 是否被篡改（哈希变化即告警）
        hash_tampered = False
        if (
            self._last_trigger_hash
            and current.trigger_hash
            and current.trigger_hash != self._last_trigger_hash
        ):
            hash_tampered = True
        if current.trigger_hash:
            self._last_trigger_hash = current.trigger_hash

        self._render_proof_label(current, hash_tampered)

        if current.error or not current.database_available:
            self._set_status("danger", "需检查", current.error or "无法读取保护状态")
        elif not current.trigger_intact:
            self._set_status(
                "danger", "触发器异常", current.trigger_issue or "触发器定义不符合预期"
            )
        elif hash_tampered:
            self._set_status("danger", "触发器被篡改", "触发器 SQL 已改变，请立即检查")
        elif current.codex_running and rate.row_rate > 0:
            self._set_status("danger", "写入泄漏", "Codex 正在写入 logs 表，触发器未生效")
        elif current.codex_running:
            self._set_status("safe", "已守护", "Codex 运行中 · 日志未增长")
        else:
            self._set_status("safe", "已守护", "Codex 未运行 · 保护已就绪")

    def _render_proof_label(self, sample: Sample, hash_tampered: bool) -> None:
        """显示触发器定义和哈希完整性。"""
        parts: list[str] = []
        if sample.trigger_intact:
            parts.append("定义 ✓")
        elif sample.trigger_issue:
            parts.append(f"定义 ✗ {sample.trigger_issue}")
        if sample.trigger_hash:
            if hash_tampered:
                parts.append("哈希 ✗ 已变更")
            else:
                parts.append(f"哈希 {sample.trigger_hash[:8]}")
        self.proof_label.setText("  ·  ".join(parts))
        color = COLORS["green"] if sample.trigger_intact and not hash_tampered else COLORS["red"]
        self.proof_label.setStyleSheet(f"color: {color};")

    def _set_status(self, level: str, title: str, context: str) -> None:
        colors = {
            "safe": COLORS["green"],
            "warning": COLORS["amber"],
            "danger": COLORS["red"],
        }
        color = colors[level]
        self.panel.set_accent(color)
        self.status_dot.set_state(color, pulsing=level in ("warning", "danger"), state_name=title)
        self.state_label.setText(title)
        self.state_label.setStyleSheet(f"color: {color};")
        self.context_label.setText(context)

    # ── Usage refresh ──

    def refresh_usage(self) -> None:
        if self._usage_loading:
            return
        self._usage_loading = True
        self.refresh_button.setEnabled(False)
        self.plan_badge.setText("刷新中")
        worker = UsageWorker()
        worker.signals.finished.connect(self._apply_usage)
        self._thread_pool.start(worker)

    def _apply_usage(self, info: UsageInfo) -> None:
        self._usage_loading = False
        self.refresh_button.setEnabled(True)
        plan = (info.plan_type or "本地").upper()
        self.plan_badge.setText(plan)
        self.local_usage.setText(
            f"本机 {self._format_tokens(info.local_tokens_total)} · {info.local_thread_count} 任务"
        )
        for bar in self.quota_bars:
            bar.hide()
        if info.source == "remote" and info.windows:
            self.quota_fallback.hide()
            for bar, window in zip(self.quota_bars, info.windows):
                bar.set_window(window.label, window.remaining_percent, format_reset_time(window.resets_at))
                bar.show()
            remaining = min(window.remaining_percent for window in info.windows)
            self._quota_percent = remaining
            self.status_dot.set_quota(remaining)
            self._render_quota_summary()
            compact_color = (
                COLORS["red"] if remaining <= 10
                else COLORS["amber"] if remaining <= 25
                else COLORS["green"]
            )
            self.compact_quota.setStyleSheet(f"color: {compact_color};")
            badge_bg = COLORS["red_bg"] if (info.limit_reached or not info.allowed) else COLORS["green_bg"]
            badge_fg = COLORS["red"] if (info.limit_reached or not info.allowed) else COLORS["green"]
            self.plan_badge.setStyleSheet(
                f"padding: 2px 6px; background: {badge_bg}; color: {badge_fg}; border-radius: 3px;"
            )
        else:
            self._quota_percent = None
            self.status_dot.set_quota(None)
            self._render_quota_summary()
            self.compact_quota.setStyleSheet(f"color: {COLORS['muted']};")
            self.quota_fallback.setText("官方额度暂不可用，仅显示本机累计 token。")
            self.quota_fallback.setToolTip(info.error or "")
            self.quota_fallback.show()
            self.plan_badge.setStyleSheet(
                f"padding: 2px 6px; background: {COLORS['surface_alt']};"
                f" color: {COLORS['muted']}; border-radius: 3px;"
            )

    # ── Formatters ──

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value < 1024:
            return f"{value} B"
        if value < 1024**2:
            return f"{value / 1024:.1f} KB"
        return f"{value / 1024**2:.2f} MB"

    @staticmethod
    def _format_tokens(value: int) -> str:
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return str(value)

    @staticmethod
    def _format_count(value: int) -> str:
        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return str(value)


def main() -> None:
    global _instance_lock
    lock_path = Path(tempfile.gettempdir()) / "codex-monitor-widget.lock"
    _instance_lock = QLockFile(str(lock_path))
    _instance_lock.setStaleLockTime(0)
    if not _instance_lock.tryLock(100):
        return
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Codex Monitor")
    app.setOrganizationName("CodexMonitor")
    app.setQuitOnLastWindowClosed(True)
    widget = FloatingWidget()
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
