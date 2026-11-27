"""Identidad visual de eREDES: paletas claro/oscuro y hoja de estilos Qt (QSS)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    sidebar: str
    sidebar_text: str
    sidebar_muted: str
    sidebar_active: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    primary: str
    primary_hover: str
    primary_text: str
    accent: str
    success: str
    warning: str
    danger: str
    info: str
    selection: str


# Colores del sistema de Apple (Human Interface Guidelines) — modo oscuro
DARK = Palette(
    name="dark", bg="#1E1E1E", sidebar="#262628", sidebar_text="#E5E5EA", sidebar_muted="#8E8E93",
    sidebar_active="#3A3A3C", surface="#2C2C2E", surface2="#3A3A3C", border="#3D3D40", text="#F5F5F7",
    muted="#98989D", primary="#0A84FF", primary_hover="#409CFF", primary_text="#FFFFFF", accent="#64D2FF",
    success="#30D158", warning="#FF9F0A", danger="#FF453A", info="#64D2FF", selection="#0A3A6B",
)

# Colores del sistema de Apple — modo claro (por defecto)
LIGHT = Palette(
    name="light", bg="#F5F5F7", sidebar="#EBEBF0", sidebar_text="#1D1D1F", sidebar_muted="#86868B",
    sidebar_active="#DCDCE1", surface="#FFFFFF", surface2="#F2F2F7", border="#E5E5EA", text="#1D1D1F",
    muted="#6E6E73", primary="#007AFF", primary_hover="#2A8CFF", primary_text="#FFFFFF", accent="#32ADE6",
    success="#34C759", warning="#FF9500", danger="#FF3B30", info="#5AC8FA", selection="#D3E6FF",
)

_current: Palette = LIGHT


def current() -> Palette:
    return _current


def set_current(name: str) -> Palette:
    global _current
    _current = LIGHT if name == "light" else DARK
    return _current


def status_color(status: str) -> str:
    p = _current
    s = (status or "").upper()
    if s in ("OK", "CORRECTO", "ACTIVO", "EN LÍNEA", "VALIDA", "VÁLIDA", "COMPATIBLE"):
        return p.success
    if s in ("ERROR", "INVALIDA", "INVÁLIDA", "BLOQUEADO", "EN_USO"):
        return p.danger
    if s in ("DESCONECTADO", "SIN RESPUESTA", "SIN_FIRMA", "AVISO", "NO_CONFIRMADA"):
        return p.warning
    return p.muted


def qss(p: Palette, check_img: str = "") -> str:
    check = f"image: url({check_img});" if check_img else "image: none;"
    return f"""
* {{ outline: 0; }}
QWidget {{ color: {p.text}; font-size: 15px; }}
QMainWindow, QDialog, #PageRoot, QStackedWidget, QScrollArea, #ScrollContent {{ background: {p.bg}; }}
QToolTip {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; padding: 6px; border-radius: 6px; }}

/* ---------- Sidebar ---------- */
#Sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
#Sidebar QLabel {{ color: {p.sidebar_text}; background: transparent; }}
#BrandName {{ color: {p.sidebar_text}; font-size: 20px; font-weight: 700; }}
#BrandSub {{ color: {p.sidebar_muted}; font-size: 9px; letter-spacing: 0.6px; font-weight: 600; }}
#NavSection {{ color: {p.sidebar_muted}; font-size: 12px; font-weight: 600; padding: 16px 20px 4px 20px; }}
#NavButton {{ background: transparent; color: {p.sidebar_text}; border: none; border-radius: 7px; text-align: left;
              padding: 9px 10px; margin: 1px 10px; font-size: 15px; font-weight: 400; }}
#NavButton:hover {{ background: {p.sidebar_active}; }}
#NavButton:checked {{ background: {p.primary}; color: #FFFFFF; font-weight: 500; }}
#UpdateButton {{ background: transparent; color: {p.primary}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 6px 10px; font-size: 13px; font-weight: 600; }}
#UpdateButton:hover {{ background: {p.sidebar_active}; }}
#SidebarFooter {{ color: {p.sidebar_muted}; font-size: 11px; padding: 10px 18px; }}

/* ---------- Topbar ---------- */
#TopBar {{ background: {p.bg}; border-bottom: 1px solid {p.border}; }}
#PageTitle {{ font-size: 26px; font-weight: 700; letter-spacing: -0.3px; }}
#PageSubtitle {{ color: {p.muted}; font-size: 14px; }}

/* ---------- Cards ---------- */
#Card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
#Card QLabel {{ background: transparent; }}
#CardTitle {{ font-size: 17px; font-weight: 600; }}
#CardSubtitle, #Muted {{ color: {p.muted}; }}
#StatValue {{ font-size: 30px; font-weight: 600; letter-spacing: -0.4px; }}
#StatLabel {{ color: {p.muted}; font-size: 14px; font-weight: 500; }}
#StatHint {{ color: {p.muted}; font-size: 14px; }}
#IconBubble {{ border-radius: 12px; }}
#Badge {{ border-radius: 10px; padding: 3px 10px; font-size: 13px; font-weight: 600; }}
#Banner {{ border-radius: 10px; padding: 10px 14px; }}

/* ---------- Buttons ---------- */
QPushButton {{ background: {p.surface}; color: {p.text}; border: 1px solid {"#D1D1D6" if p.name == "light" else "#48484A"};
               border-radius: 8px; padding: 9px 16px; font-weight: 500; font-size: 15px; }}
QPushButton:hover {{ background: {p.surface2}; }}
QPushButton::menu-indicator {{ image: none; width: 0px; }}
QPushButton:pressed {{ background: {p.selection}; }}
QPushButton:disabled {{ color: {p.muted}; background: {p.surface}; border-color: {p.border}; }}
QPushButton#Primary {{ background: {p.primary}; color: {p.primary_text}; border: 1px solid {p.primary}; }}
QPushButton#Primary:hover {{ background: {p.primary_hover}; }}
QPushButton#Primary:disabled {{ background: {p.surface2}; color: {p.muted}; border-color: {p.border}; }}
QPushButton#Danger {{ background: transparent; color: {p.danger}; border: 1px solid {p.danger}; }}
QPushButton#Danger:hover {{ background: {p.danger}; color: white; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; }}
QPushButton#Ghost:hover {{ background: {p.surface2}; border-color: {p.border}; }}
QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 6px; }}
QToolButton:hover {{ background: {p.surface2}; }}

/* ---------- Inputs ---------- */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QDateTimeEdit {{
    background: {p.surface}; border: 1px solid {"#D1D1D6" if p.name == "light" else "#48484A"}; border-radius: 8px; padding: 8px 10px; font-size: 15px;
    selection-background-color: {p.primary}; selection-color: white; }}
QLineEdit, QSpinBox, QComboBox {{ min-height: 24px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 2px solid {"#7FB6FF" if p.name == "light" else "#0A84FF"}; padding: 7px 9px; }}
QLineEdit[invalid="true"] {{ border: 1px solid {p.danger}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {p.muted}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border}; selection-background-color: {p.selection};
    selection-color: {p.text}; padding: 4px; outline: 0; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {"#C7C7CC" if p.name == "light" else "#5A5A5E"}; background: {p.surface}; }}
QCheckBox::indicator:checked {{ background: {p.primary}; border-color: {p.primary}; {check} }}
QRadioButton {{ background: transparent; }}
QFormLayout QLabel, QLabel#FormLabel {{ color: {p.muted}; font-weight: 500; }}

/* ---------- Tables / Trees ---------- */
QTableWidget, QTableView, QTreeWidget, QListWidget {{ background: {p.surface}; alternate-background-color: {p.surface2};
    border: 1px solid {p.border}; border-radius: 10px; gridline-color: transparent; selection-background-color: {p.selection};
    selection-color: {p.text}; }}
QTableWidget::item, QTreeWidget::item, QListWidget::item {{ padding: 6px 6px; border: none; font-size: 15px; }}
QHeaderView::section {{ background: {p.surface}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border};
    padding: 8px 6px; font-weight: 600; font-size: 13px; }}
QTableCornerButton::section {{ background: {p.surface}; border: none; }}
QListWidget#Master::item {{ border-radius: 8px; margin: 1px 4px; padding: 7px 8px; }}
QListWidget#Master::item:selected {{ background: {p.selection}; color: {p.text}; }}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- Progress ---------- */
QProgressBar {{ background: {p.surface2}; border: none; border-radius: 5px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 5px; background: {p.primary}; }}

/* ---------- Tabs ---------- */
QTabWidget::pane {{ border: none; }}
QTabBar {{ background: {p.surface2}; border-radius: 8px; }}
QTabBar::tab {{ background: transparent; color: {p.text}; padding: 5px 16px; margin: 2px; border-radius: 6px; font-weight: 500; }}
QTabBar::tab:selected {{ background: {p.surface}; border: 1px solid {p.border}; }}
QTabBar::tab:hover {{ color: {p.text}; }}

QSplitter::handle {{ background: transparent; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.selection}; }}
QStatusBar {{ background: {p.bg}; color: {p.muted}; border-top: 1px solid {p.border}; }}
QStatusBar QLabel {{ color: {p.muted}; }}
QMessageBox {{ background: {p.surface}; }}
#Toast {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 14px; }}
#Toast QLabel {{ background: transparent; }}
#StepRow {{ background: transparent; }}
#Mono {{ font-family: "SF Mono", "Menlo", "Consolas", monospace; font-size: 13px; }}
"""
