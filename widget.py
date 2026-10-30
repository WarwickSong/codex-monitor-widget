"""PySide6 悬浮条：折叠为胶囊小条（发光点+数字+迷你进度），悬停展开玻璃面板。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import QEasingCurve, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QMenu, QProgressBar, QVBoxLayout, QWidget,
)

from config import (
    COLOR_AMBER, COLOR_GREEN, COLOR_RED, COLLAPSED_H, EXPAND_MS,
    PANEL_H, PANEL_W, USAGE_PAGE,
)
from forecast import Forecast, forecast
from history import append_sample, load_samples, load_settings, save_settings
from tokens import fmt_tok
from usage import UsageSnapshot

TEXT_MAIN = "#f2f2f5"
TEXT_MUTED = "#9a9aa2"
TEXT_FAINT = "#70707a"
HAIRLINE = "rgba(255,255,255,0.08)"
CARD_BG = "rgba(255,255,255,0.055)"

SHELL_QSS = f"""
#shell {{ background: rgba(16,16,22,0.90); border-radius: 16px;
          border: 1px solid rgba(255,255,255,0.13); }}
#bar_pct {{ color: {TEXT_MAIN}; font-size: 12px; font-weight: 600; }}
#bar_days {{ color: {TEXT_MUTED}; font-size: 11px; }}
#caption {{ color: {TEXT_FAINT}; font-size: 10px; }}
#pct {{ font-size: 26px; font-weight: 700; }}
#countdown {{ color: {TEXT_MUTED}; font-size: 11px; }}
#muted {{ color: {TEXT_MUTED}; font-size: 11px; }}
#card {{ background: {CARD_BG}; border-radius: 10px;
         border: 1px solid {HAIRLINE}; }}
#card_title {{ color: {TEXT_FAINT}; font-size: 10px; }}
#card_main {{ color: {TEXT_MAIN}; font-size: 14px; font-weight: 650; }}
#card_sub {{ color: {TEXT_MUTED}; font-size: 11px; }}
#bar_progress, #mini_progress {{
    background: rgba(255,255,255,0.10); border: none; border-radius: 2px; }}
#bar_progress {{ max-height: 5px; min-height: 5px; }}
#mini_progress {{ max-height: 4px; min-height: 4px; max-width: 36px; }}
QMenu {{ background: rgba(22,22,28,0.97); color: {TEXT_MAIN};
         border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 8px; font-size: 12px; }}
QMenu::item:selected {{ background: rgba(255,255,255,0.09); }}
QMenu::separator {{ height: 1px; background: {HAIRLINE}; margin: 5px 8px; }}
"""


def _status_color(remain_pct: float) -> str:
    if remain_pct > 40:
        return COLOR_GREEN
    if remain_pct > 15:
        return COLOR_AMBER
    return COLOR_RED


def _lighter(hex_color: str, factor: float = 1.45) -> QColor:
    c = QColor(hex_color)
    r = min(255, int(c.red() + (255 - c.red()) * (factor - 1)))
    g = min(255, int(c.green() + (255 - c.green()) * (factor - 1)))
    b = min(255, int(c.blue() + (255 - c.blue()) * (factor - 1)))
    return QColor(r, g, b)


def _progress_qss(color: str, obj: str) -> str:
    hi = _lighter(color).name()
    return (f"{obj}::chunk {{ border-radius: 2px;"
            f" background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f" stop:0 {color}, stop:1 {hi}); }}")


class StatusDot(QWidget):
    """发光状态点：实心圆 + 柔和光晕。"""

    def __init__(self, color: str = TEXT_MUTED) -> None:
        super().__init__()
        self.setFixedSize(18, 18)
        self._color = QColor(color)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        halo = QRadialGradient(9.0, 9.0, 9.0)
        halo.setColorAt(0.0, QColor(self._color.red(), self._color.green(),
                                    self._color.blue(), 120))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(halo))
        p.drawEllipse(0, 0, 18, 18)
        p.setBrush(self._color)
        p.drawEllipse(6, 6, 6, 6)


def _fmt_countdown(resets_ts) -> str:
    if not resets_ts:
        return "重置时间未知"
    secs = max(0, int((datetime.fromtimestamp(resets_ts) - datetime.now()).total_seconds()))
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    return f"{d}天{h}时" if d else f"{h}时{r // 60}分"


class QuotaWidget(QWidget):
    updated = Signal(object)        # submit → emit → 队列投递到 UI 线程
    tokens_updated = Signal(object)  # token 统计同理

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._snapshot: Optional[UsageSnapshot] = None
        self._forecast: Optional[Forecast] = None
        self._tokens: Optional[dict] = None
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
        self.tokens_updated.connect(self._on_tokens)
        self._restore_pos()
        self._apply_state()

    # ---- UI 构建 ----
    def _build_ui(self) -> None:
        shell = QFrame(self, objectName="shell")
        shell.setStyleSheet(SHELL_QSS)
        shadow = QGraphicsDropShadowEffect(shell)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(0, 0, 0, 150))
        shell.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        lay = QVBoxLayout(shell)
        lay.setContentsMargins(12, 3, 12, 7)
        lay.setSpacing(8)

        # —— 折叠态：发光点 + 数字 + 迷你进度 + 天数 ——
        self.bar_row = QWidget()
        row = QHBoxLayout(self.bar_row)
        row.setContentsMargins(2, 0, 4, 0)
        row.setSpacing(7)
        self.dot = StatusDot()
        self.bar_pct = QLabel("--", objectName="bar_pct")
        self.mini = QProgressBar(objectName="mini_progress")
        self.mini.setTextVisible(False)
        self.mini.setRange(0, 100)
        self.mini.setFixedWidth(36)
        self.bar_days = QLabel("", objectName="bar_days")
        row.addWidget(self.dot, 0, Qt.AlignVCenter)
        row.addWidget(self.bar_pct, 0, Qt.AlignVCenter)
        row.addWidget(self.mini, 0, Qt.AlignVCenter)
        row.addWidget(self.bar_days, 0, Qt.AlignVCenter)
        lay.addWidget(self.bar_row)

        # —— 展开面板 ——
        self.panel = QFrame(objectName="panel")
        lay.addWidget(self.panel)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(4, 6, 4, 4)
        pl.setSpacing(8)

        header = QVBoxLayout()
        header.setSpacing(2)
        self.caption = QLabel("周限额剩余", objectName="caption")
        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        self.pct = QLabel("--", objectName="pct")
        self.countdown_wrap = QLabel("", objectName="countdown")
        head_row.addWidget(self.pct)
        head_row.addStretch(1)
        head_row.addWidget(self.countdown_wrap, 0, Qt.AlignBottom)
        header.addWidget(self.caption)
        header.addLayout(head_row)
        pl.addLayout(header)

        self.progress = QProgressBar(objectName="bar_progress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        pl.addWidget(self.progress)

        cards = QHBoxLayout()
        cards.setSpacing(8)
        self.wd_card = self._make_card("工作日节奏")
        self.ed_card = self._make_card("每天都在用")
        cards.addWidget(self.wd_card)
        cards.addWidget(self.ed_card)
        pl.addLayout(cards)

        # Token 视角（仅展开时可见，折叠条不变）
        self.tok_used = QLabel("", objectName="muted")
        self.tok_fit = QLabel("", objectName="muted")
        self.tok_used.setWordWrap(True)
        self.tok_fit.setWordWrap(True)
        pl.addWidget(self.tok_used)
        pl.addWidget(self.tok_fit)

        self.extra = QLabel("", objectName="muted")
        self.footer = QLabel("", objectName="muted")
        self.extra.setWordWrap(True)
        self.footer.setWordWrap(True)
        pl.addWidget(self.extra)
        pl.addWidget(self.footer)

        self.panel.hide()
        self.setGeometry(self.x(), self.y(), self._bar_width(), COLLAPSED_H)

    def _make_card(self, title: str) -> QFrame:
        card = QFrame(objectName="card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(3)
        v.addWidget(QLabel(title, objectName="card_title"))
        card.main_label = QLabel("--", objectName="card_main")
        card.sub_label = QLabel("", objectName="card_sub")
        card.sub_label.setWordWrap(True)
        v.addWidget(card.main_label)
        v.addWidget(card.sub_label)
        return card

    def _bar_width(self) -> int:
        return max(112, self.bar_row.sizeHint().width() + 26)

    # ---- 位置与生命周期 ----
    def _restore_pos(self) -> None:
        pos = load_settings().get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            g = QApplication.primaryScreen().availableGeometry()
            self.move(g.right() - 160, g.bottom() - 80)

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

    def submit_tokens(self, stats: dict) -> None:
        self.tokens_updated.emit(stats)  # 任意线程可调用

    def _on_tokens(self, stats: dict) -> None:
        self._tokens = stats
        self._apply_state()

    def _render_tokens(self, remain: float) -> None:
        t = self._tokens
        if t is None:
            self.tok_used.setText("token 统计扫描中…")
            self.tok_fit.setText("")
            return
        hit = t.get("hit_rate")
        hit_txt = f" · 命中 {hit * 100:.0f}%" if hit is not None else ""
        self.tok_used.setText(
            f"本周期 token：入 {fmt_tok(t['in_t'])} · 缓存 {fmt_tok(t['cached'])}"
            f" · 出 {fmt_tok(t['out'])}{hit_txt}"
        )
        per_pct = t.get("per_pct")
        if per_pct:
            self.tok_fit.setText(
                f"实测 1% ≈ {fmt_tok(per_pct)} token · 剩余 ≈ {fmt_tok(per_pct * remain)}"
            )
        else:
            self.tok_fit.setText("token↔% 汇率积累中（多用几轮更准）")

    def _set_collapsed(self, color: str, main: str, days: str, progress: int) -> None:
        self.dot.set_color(color)
        self.bar_pct.setText(main)
        self.bar_days.setText(days)
        self.mini.setValue(progress)
        self.mini.setStyleSheet(_progress_qss(color, "#mini_progress"))

    def _apply_state(self) -> None:
        snap = self._snapshot
        if snap is None:
            self._set_collapsed(TEXT_MUTED, "加载中…", "", 0)
            return
        if snap.weekly is None:
            err = snap.error or ""
            if ("未找到" in err) or ("令牌" in err) or ("登录" in err):
                self._set_collapsed(COLOR_RED, "未登录", "", 0)
                self.footer.setText(f"未登录：{err}")
            else:
                self._set_collapsed(TEXT_MUTED, "离线", "", 0)
                self.footer.setText(f"拉取失败：{err[:60]}" if err else "拉取失败")
            return

        w = snap.weekly
        remain = 100.0 - w.used_pct
        color = TEXT_MUTED if snap.error else _status_color(remain)
        days_txt = ""
        if w.resets_at:
            d = max((datetime.fromtimestamp(w.resets_at) - datetime.now()).total_seconds() / 86400, 0.0)
            days_txt = f"{d:.1f}d" + ("!" if snap.error else "")
        self._set_collapsed(color, f"{remain:.0f}%", days_txt, int(remain))

        self.pct.setText(f"{remain:.0f}%")
        self.pct.setStyleSheet(f"color: {color};")
        self.progress.setValue(int(remain))
        self.progress.setStyleSheet(_progress_qss(color, "#bar_progress"))
        self.countdown_wrap.setText(f"重置 {_fmt_countdown(w.resets_at)}")

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
        self._render_tokens(remain)
        extra = (f"5h 窗口剩余 {100 - snap.five_hour.used_pct:.0f}%"
                 if snap.five_hour else "")
        self.extra.setText(extra)
        self.extra.setVisible(bool(extra))
        ts = datetime.fromtimestamp(snap.fetched_at).strftime("%H:%M")
        err_txt = (snap.error[:60] + "…") if snap.error and len(snap.error) > 60 else (snap.error or "")
        self.footer.setText(f"更新 {ts}" + (f" · {err_txt}" if err_txt else ""))
        if not self._expanded:
            self.setGeometry(self.x(), self.y(), self._bar_width(), COLLAPSED_H)

    def _fill_card(self, card, sc, remaining_h: float, unit: str, tag: str) -> None:
        sub = ""
        if sc.end_remaining_pct >= 0:
            card.main_label.setText(f"结余 {sc.end_remaining_pct:.0f}%")
            card.main_label.setStyleSheet(f"color: {COLOR_GREEN};")
        else:
            ahead = max(0.0, remaining_h - sc.hours_left)
            card.main_label.setText(f"超 {-sc.end_remaining_pct:.0f}%")
            card.main_label.setStyleSheet(f"color: {COLOR_RED};")
            if ahead > 0:
                sub += f"提前{ahead:.0f}h用完 · "
        sub += f"撑 {sc.days_left:.1f} {unit}"
        if tag:
            sub += f" · {tag}"
        card.sub_label.setText(sub)
