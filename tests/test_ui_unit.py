"""伏せ表示・言語切り替え・テーマの単体テスト."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MSM_DATA_DIR", tempfile.mkdtemp(prefix="msm_unit_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_privacy_masking():
    from msm.gui import privacy

    privacy.set_enabled(True)
    home = str(Path.home())
    assert privacy.mask_path(home + r"\MinecraftServers\a") == r"%USERPROFILE%\MinecraftServers\a"
    t = privacy.text("LAN: 192.168.1.20:25565 / local 127.0.0.1:25565")
    assert "192.168" not in t and "127.0.0.1" in t
    line = "[12:00:00] [Server thread/INFO]: Steve[/203.0.113.9:51234] logged in with entity id 1"
    assert "203.0.113.9" not in privacy.console(line) and "Steve" in privacy.console(line)
    assert privacy.console("NeoForge 26.1.2.126 loaded") == "NeoForge 26.1.2.126 loaded"  # バージョンは伏せない
    assert privacy.to_real_path(r"%USERPROFILE%\x").startswith(home)
    privacy.set_enabled(False)
    assert privacy.text("192.168.1.20") == "192.168.1.20"
    privacy.set_enabled(True)


def test_secret_label():
    from PySide6.QtWidgets import QApplication

    from msm.gui import privacy

    app = QApplication.instance() or QApplication(sys.argv)
    privacy.set_enabled(True)
    w = privacy.SecretLabel("-")
    w.set_value("abc.joinmc.link")
    assert w.label.text() == privacy.MASK and w.value() == "abc.joinmc.link"
    w.eye.setChecked(True)
    assert w.label.text() == "abc.joinmc.link"
    w.set_value("")
    assert w.label.text() == "-"
    app.processEvents()


def test_i18n_tr():
    from msm.gui import i18n

    i18n._lang = "en"
    try:
        assert i18n.tr("停止中") == "Stopped"
        assert i18n.tr(" 分ごと (0で無効)") == " min interval (0 = off)"
        assert i18n.tr("停止中\n起動中") == "Stopped\nStarting"
        assert i18n.tr("辞書に無い文") == "辞書に無い文"
        assert i18n.T("あ", "a") == "a"
    finally:
        i18n._lang = "ja"
    assert i18n.tr("停止中") == "停止中"


def test_theme_styles():
    from msm.gui import theme

    for dark in (False, True):
        qss = theme.build_style(dark)
        assert "QPushButton#primary" in qss and "{" in qss and qss.count("{") == qss.count("}")
