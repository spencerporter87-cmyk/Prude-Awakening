INK = "#2A1B2E"
MUTED = "#6B566E"
CREAM = "#FBF6EE"
CARD = "#FFFFFF"
LINE = "#E8DDCF"
TEAL = "#2E7D8C"
TEAL_DARK = "#1E5A66"
LEAF = "#5DAA4A"
RED = "#C8412B"

QSS = f"""
QWidget {{ color: {INK}; font-family: "Segoe UI", "Inter", sans-serif; font-size: 10.5pt; }}
QMainWindow, QDialog, #Page {{ background: {CREAM}; }}
QLabel#H1 {{ font-size: 20pt; font-weight: 600; }}
QLabel#H2 {{ font-size: 13pt; font-weight: 600; }}
QLabel#Muted, QLabel.muted {{ color: {MUTED}; }}
QLabel#Small {{ color: {MUTED}; font-size: 9pt; }}
QFrame#Card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#Drop {{ background: {CARD}; border: 2px dashed #CDBBA4; border-radius: 20px; }}
QFrame#Drop[hover="true"] {{ border-color: {TEAL}; background: #F1F8F9; }}
QPushButton {{ background: {CARD}; border: 1px solid #D9CBB8; border-radius: 10px;
               padding: 8px 16px; min-height: 22px; }}
QPushButton:hover {{ border-color: {TEAL}; }}
QPushButton:disabled {{ color: #A99AA9; border-color: {LINE}; }}
QPushButton#Primary {{ background: {TEAL}; color: white; border: none; font-weight: 600;
                       padding: 10px 22px; }}
QPushButton#Primary:hover {{ background: {TEAL_DARK}; }}
QPushButton#Primary:disabled {{ background: #9CC3CA; color: #EEF6F7; }}
QPushButton#Danger {{ color: {RED}; }}
QPushButton#Link {{ background: transparent; border: none; color: {TEAL}; padding: 4px; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QPushButton#Icon {{ padding: 6px 10px; min-width: 20px; }}
QCheckBox, QRadioButton {{ spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; }}
QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background: white; border: 1px solid #D9CBB8; border-radius: 8px; padding: 5px 8px; }}
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus {{ border-color: {TEAL}; }}
QProgressBar {{ background: #EFE6D9; border: none; border-radius: 6px; height: 12px; text-align: center;
                color: transparent; }}
QProgressBar::chunk {{ background: {TEAL}; border-radius: 6px; }}
QSlider::groove:horizontal {{ height: 6px; background: #E6DACB; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {TEAL}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; border: 2px solid {TEAL}; width: 16px; height: 16px;
                               margin: -7px 0; border-radius: 9px; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 12px; background: white; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 18px; margin-right: 4px; color: {MUTED};
                border-bottom: 3px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom-color: {TEAL}; }}
QTableWidget {{ background: white; border: none; gridline-color: #F1EADF; selection-background-color: #E3F0F2;
                selection-color: {INK}; alternate-background-color: #FCFAF6; }}
QTableView::indicator {{ width: 18px; height: 18px; border: 1.5px solid #B8A99A; border-radius: 5px;
                         background: white; }}
QTableView::indicator:checked {{ background: {TEAL}; border-color: {TEAL}; }}
QHeaderView::section {{ background: #F7F1E8; border: none; border-bottom: 1px solid {LINE};
                        padding: 6px 8px; font-weight: 600; color: {MUTED}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {INK}; color: white; border: none; padding: 6px; }}
"""
