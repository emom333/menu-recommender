# PyInstaller build specification for Dentec Lab Manager (Windows, one-folder build).
#   pyinstaller --noconfirm DentecLab.spec
# Run from the dentec_lab folder.  Output: dist\DentecLab\DentecLab.exe

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[("dentec/ocr/labslip_template.json", "dentec/ocr")],
    hiddenimports=["sqlalchemy.dialects.sqlite", "PySide6.QtPdf", "PySide6.QtPrintSupport"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "pymupdf4llm", "pymupdf.layout", "pymupdf_layout", "onnxruntime", "networkx",
              "matplotlib", "pandas", "IPython"],
    noarchive=False,
)


def _keep(entry):
    # Drop optional PyMuPDF AI-layout models / developer files (not used; ~50 MB).
    p = entry[0].replace("\\", "/")
    return not any(x in p for x in ("pymupdf/layout", "pymupdf/mupdf-devel", "onnxruntime"))


a.datas = [d for d in a.datas if _keep(d)]
a.binaries = [b for b in a.binaries if _keep(b)]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DentecLab",
    console=False,  # windowed application
    icon=None,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DentecLab")
