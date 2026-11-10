"""Application stylesheet: plain, high-contrast, readable."""

QSS = """
* { font-family: "Segoe UI", "Arial"; font-size: 10pt; }
QMainWindow, QDialog { background: #f6f7f9; }
#Sidebar { background: #1f2933; border: none; min-width: 205px; max-width: 205px; }
#Sidebar::item { color: #e4e7eb; padding: 11px 14px; border-left: 4px solid transparent; }
#Sidebar::item:selected { background: #323f4b; color: #ffffff; border-left: 4px solid #ffffff; font-weight: bold; }
#Sidebar::item:hover { background: #2a3540; }
#Brand { color: #ffffff; font-size: 12pt; font-weight: bold; padding: 14px 12px 2px 14px; background: #1f2933; }
#BrandSub { color: #9aa5b1; padding: 0px 12px 12px 14px; background: #1f2933; font-size: 9pt; }
#UserBox { color: #cbd2d9; background: #1f2933; padding: 10px 14px; font-size: 9pt; }
#PageTitle { font-size: 16pt; font-weight: bold; color: #1f2933; padding-bottom: 2px; }
#Section { font-size: 11pt; font-weight: bold; color: #1f2933; padding-top: 6px; }
#Hint { color: #52606d; font-size: 9pt; }
#Warn { color: #8a4b00; background: #fff3cd; border: 1px solid #f0d58c; padding: 6px; }
#Ok { color: #1e5631; background: #e3f4e8; border: 1px solid #a7d7b5; padding: 6px; }
#Card { background: #ffffff; border: 1px solid #d9dde3; border-radius: 6px; }
#CardTitle { color: #52606d; font-size: 9pt; font-weight: bold; }
#CardValue { color: #1f2933; font-size: 17pt; font-weight: bold; }
QPushButton { background: #ffffff; border: 1px solid #9aa5b1; border-radius: 4px; padding: 5px 12px; min-height: 20px; }
QPushButton:hover { background: #eef1f4; }
QPushButton:disabled { color: #9aa5b1; border-color: #d9dde3; }
QPushButton#Primary { background: #1f2933; color: #ffffff; border: 1px solid #1f2933; font-weight: bold; }
QPushButton#Primary:hover { background: #323f4b; }
QPushButton#Primary:disabled { background: #9aa5b1; border-color: #9aa5b1; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit, QTextEdit {
    background: #ffffff; border: 1px solid #b8c0c9; border-radius: 3px; padding: 3px 5px; min-height: 20px; }
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QPlainTextEdit:focus, QSpinBox:focus { border: 1px solid #1f2933; }
QLineEdit[uncertain="true"], QComboBox[uncertain="true"] { background: #fff3cd; border: 1px solid #d39e00; }
QTableWidget, QTableView, QListWidget { background: #ffffff; border: 1px solid #d9dde3; gridline-color: #e4e7eb;
    alternate-background-color: #f8f9fa; selection-background-color: #cfd8e3; selection-color: #1f2933; }
QHeaderView::section { background: #eef1f4; border: none; border-bottom: 1px solid #b8c0c9;
    border-right: 1px solid #d9dde3; padding: 5px; font-weight: bold; }
QTabWidget::pane { border: 1px solid #d9dde3; background: #ffffff; }
QTabBar::tab { background: #eef1f4; padding: 6px 14px; border: 1px solid #d9dde3; border-bottom: none; }
QTabBar::tab:selected { background: #ffffff; font-weight: bold; }
QGroupBox { border: 1px solid #d9dde3; border-radius: 4px; margin-top: 12px; background: #ffffff; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; font-weight: bold; }
QStatusBar { background: #eef1f4; }
"""
