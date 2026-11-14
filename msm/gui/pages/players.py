"""プレイヤー管理 (コンソールコマンド経由)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QVBoxLayout

from msm.gui import common as C
from msm.server import players as pl
from msm.server.process import registry


class PlayersPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("プレイヤー", "サーバーのコンソールコマンドで操作します (RCONは使いません)。操作はサーバー稼働中のみ可能です。"))
        self.count = QLabel()
        lay.addWidget(self.count)
        row = QHBoxLayout()
        g = QGroupBox("接続中のプレイヤー")
        gl = QVBoxLayout(g)
        self.online = QListWidget()
        self.online.currentTextChanged.connect(lambda t: self.name.setText(t) if t else None)
        self.online.setContextMenuPolicy(Qt.CustomContextMenu)
        self.online.customContextMenuRequested.connect(self._menu)
        gl.addWidget(self.online)
        gl.addWidget(C.button("一覧を更新 (listコマンド)", self.update_list))
        row.addWidget(g)
        for title, attr in (("ホワイトリスト", "wl"), ("OP", "ops"), ("BAN中", "ban")):
            gg = QGroupBox(title)
            gv = QVBoxLayout(gg)
            lw = QListWidget()
            lw.currentTextChanged.connect(lambda t: self.name.setText(t.split(" ")[0]) if t else None)
            setattr(self, attr, lw)
            gv.addWidget(lw)
            row.addWidget(gg)
        lay.addLayout(row)
        g2 = QGroupBox("操作")
        g2l = QVBoxLayout(g2)
        self.name = QLineEdit()
        self.name.setPlaceholderText("プレイヤー名")
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("理由 (キック・BAN時、任意)")
        g2l.addLayout(C.hbox(self.name, self.reason, stretch_end=False))
        self.btns = [
            C.button("キック", lambda: self.act("kick")), C.button("BAN", lambda: self.act("ban"), "danger"),
            C.button("BAN解除", lambda: self.act("pardon")), C.button("OP付与", lambda: self.act("op")),
            C.button("OP解除", lambda: self.act("deop")), C.button("ホワイトリスト追加", lambda: self.act("whitelist_add")),
            C.button("ホワイトリストから削除", lambda: self.act("whitelist_remove")),
        ]
        g2l.addLayout(C.hbox(*self.btns))
        g2l.addWidget(C.help_label("※ OP権限を持つ人はサーバーのほぼすべての操作ができます。信頼できる人だけに付与してください。\n"
                                   "※ ホワイトリストを有効にするには「サーバー設定」で white-list をオンにしてください。"))
        lay.addWidget(g2)
        lay.addStretch(1)
        ctx.players_changed.connect(lambda *_: self.refresh(), Qt.QueuedConnection)
        ctx.state_changed.connect(lambda *_: self.refresh(), Qt.QueuedConnection)

    def refresh(self) -> None:
        s = self.ctx.current()
        self.setEnabled(s is not None)
        if not s:
            return
        sp = registry.find(s.id)
        running = bool(sp and sp.running)
        self.online.clear()
        if running:
            self.online.addItems(sorted(sp.players))
            self.count.setText(f"参加人数: {len(sp.players)} / {sp.max_players}")
        else:
            self.count.setText("サーバーは停止中です (一覧の表示のみ可能)")
        self.wl.clear()
        self.wl.addItems(pl.whitelist(s.path))
        self.ops.clear()
        self.ops.addItems(pl.ops(s.path))
        self.ban.clear()
        self.ban.addItems([f"{b.get('name', '')} ({b.get('reason', '')})" for b in pl.banned(s.path)])
        for b in self.btns:
            b.setEnabled(running)

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu

        it = self.online.itemAt(pos)
        if not it:
            return
        self.name.setText(it.text())
        m = QMenu(self)
        for action in ("kick", "ban", "op", "deop", "whitelist_add"):
            m.addAction(pl.COMMANDS[action][1], lambda a=action: self.act(a))
        m.exec(self.online.viewport().mapToGlobal(pos))

    def update_list(self):
        s = self.ctx.current()
        sp = registry.find(s.id) if s else None
        if sp and sp.running:
            pl.refresh_list(sp)

    def act(self, action: str):
        s = self.ctx.current()
        sp = registry.find(s.id) if s else None
        name = self.name.text().strip()
        if not sp:
            return
        if action in ("ban", "op") and not C.confirm(self, f"{name} に「{pl.COMMANDS[action][1]}」を実行しますか？"):
            return
        try:
            msg = pl.run_action(sp, action, name, self.reason.text().strip())
            self.ctx.status.emit(msg)
        except Exception as e:  # noqa: BLE001
            C.warn(self, str(e))
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1500, self.refresh)
