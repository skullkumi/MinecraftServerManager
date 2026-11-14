"""公開設定 (playit.gg / 直接公開 / LAN限定 / その他トンネル)."""
from __future__ import annotations

import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFormLayout, QGroupBox, QLabel, QLineEdit,
                               QPlainTextEdit, QRadioButton, QVBoxLayout, QWidget)

from msm.gui import common as C
from msm.gui import privacy
from msm.java import manager as jm
from msm.publish import tunnels as T
from msm.server import instance, ports


class PublishPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("友達を招待", "友達をサーバーに招待する方法を選びます。どの方法も強制ではありません。"))
        g = QGroupBox("公開方法")
        gl = QVBoxLayout(g)
        self.group = QButtonGroup(self)
        self.radios = {}
        for key in ("lan", "playit", "direct", "custom"):
            info = T.METHOD_INFO[key]
            r = QRadioButton(info["label"])
            self.radios[key] = r
            self.group.addButton(r)
            gl.addWidget(r)
            gl.addWidget(C.help_label(f"　○ {info['merits']}\n　△ {info['demerits']}"))
            r.toggled.connect(self._method_changed)
        self.with_server = QCheckBox("サーバーと同時にトンネルを起動する (playit / その他トンネル)")
        self.stop_with = QCheckBox("サーバー停止時にトンネルも停止する")
        gl.addLayout(C.hbox(self.with_server, self.stop_with))
        gl.addLayout(C.hbox(C.button("公開方法を保存", self.save, "primary")))
        lay.addWidget(g)

        self.stack = QWidget()
        self.stack_lay = QVBoxLayout(self.stack)
        self.stack_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        self.pages = {}
        self.pages["lan"] = self._lan_page()
        self.pages["playit"] = self._playit_page()
        self.pages["direct"] = self._direct_page()
        self.pages["custom"] = self._custom_page()
        for k in ("lan", "playit", "direct", "custom"):
            self.stack_lay.addWidget(self.pages[k])
        self.pack_card = C.card("MODサーバーの場合: 友達に同じMODを渡す",
                                "参加する人もサーバーと同じMODが必要です。ランチャーに読み込むだけで同じ環境になるパックを作って渡してください。")
        self.pack_card.body.addLayout(C.hbox(C.button("友達用パックを作成...", self._make_pack, "primary")))
        lay.addWidget(self.pack_card)
        self.tlog = QPlainTextEdit()
        self.tlog.setReadOnly(True)
        self.tlog.setMaximumHeight(140)
        self.tlog.setPlaceholderText("トンネルの出力")
        lay.addWidget(self.tlog)
        lay.addStretch(1)
        ctx.tunnel_line.connect(lambda l: self.tlog.appendPlainText(privacy.text(l)), Qt.QueuedConnection)

    # ---- 各方式の画面 ----
    def _lan_page(self):
        w = QGroupBox("LAN限定")
        v = QVBoxLayout(w)
        self.lan_addr = privacy.SecretLabel("(取得できません)", big=True)
        self.lan_addr.on_copied = lambda a: self.ctx.status.emit("LANアドレスをコピーしました")
        v.addWidget(QLabel("このPCのLANアドレス (同じWi-Fiの人はこのアドレスで参加):"))
        v.addWidget(self.lan_addr)
        self.lan_local = QLabel()
        self.lan_local.setObjectName("caption")
        v.addWidget(self.lan_local)
        v.addWidget(C.help_label("同じWi-Fi・同じルーターにつながっている人は、上のアドレスで参加できます。\n"
                                 "参加できない場合は、Windowsファイアウォールの確認画面で「プライベートネットワーク」を許可してください。"))
        return w

    def _playit_page(self):
        w = QGroupBox("playit.gg")
        v = QVBoxLayout(w)
        self.pl_status = QLabel()
        self.pl_status.setWordWrap(True)
        v.addWidget(self.pl_status)
        v.addLayout(C.hbox(C.button("状態を確認", self.playit_refresh), C.button("playitを起動", self.playit_start, "primary"),
                           C.button("playitを停止", self.playit_stop)))
        v.addLayout(C.hbox(C.button("playit公式サイト (ダウンロード)", lambda: C.open_url(self, "https://playit.gg/download")),
                           C.button("トンネル設定ページ", lambda: C.open_url(self, "https://playit.gg/account/tunnels")),
                           C.button("playitの初期設定 (アカウント連携) を開く", self.playit_setup)))
        f = QFormLayout()
        self.pl_addr = QLineEdit()
        self.pl_addr.setPlaceholderText("例: example.gl.joinmc.link (自動取得できない場合はplayit.ggのトンネル設定ページからコピーして入力)")
        self.pl_eye = C.button("👁", lambda: self._toggle_secret(self.pl_addr, self.pl_eye), "icon")
        self.pl_eye.setCheckable(True)
        f.addRow("接続アドレス", C.hbox(self.pl_addr, self.pl_eye, stretch_end=False))
        v.addLayout(f)
        v.addLayout(C.hbox(C.button("アドレスを保存", self.save), C.button("アドレスをコピー", lambda: self._copy(self.pl_addr.text())),
                           C.button("playitのログから接続先を探す", self.playit_from_log)))
        self.pl_log = C.help_label("")
        v.addWidget(self.pl_log)
        v.addWidget(C.help_label(
            "初めて使う場合:\n"
            "1. 公式サイトからplayitをダウンロードしてインストール (wingetの場合: winget install DevelopedMethods.playit)\n"
            "2. 「初期設定を開く」で表示されるURLからplayit.ggアカウントと連携\n"
            "3. playit.ggのWebページで「Minecraft Java」のトンネルを作成し、ローカルポートをこのサーバーのポートに設定\n"
            "4. 表示された接続アドレスを上の欄に入力して保存\n"
            "※ 本アプリはplayitのインストーラーを自動では実行しません。"))
        return w

    def _direct_page(self):
        w = QGroupBox("直接公開 (ポート開放)")
        v = QVBoxLayout(w)
        warn = QLabel("⚠ この方法では自宅のグローバルIPアドレスを相手に教えることになります。信頼できる相手だけに教えてください。")
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#c0392b; font-weight:bold;")
        v.addWidget(warn)
        self.d_ip = QLabel("グローバルIP: (未確認)")
        self.d_ip.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.d_ip)
        v.addLayout(C.hbox(C.button("グローバルIPを確認", self.check_ip), C.button("接続テスト (外部サービス)", self.ext_test),
                           C.button("ローカル待ち受け確認", self.local_test), C.button("ファイアウォール設定を開く", self.open_fw)))
        self.fw_text = QPlainTextEdit()
        self.fw_text.setReadOnly(True)
        self.fw_text.setMinimumHeight(200)
        v.addWidget(self.fw_text)
        v.addWidget(QLabel("<b>UPnPによる自動ポート開放 (任意)</b>"))
        v.addWidget(C.help_label("ルーターがUPnPに対応していれば、ルーターの設定画面を開かずにポートを開放できます。"
                                 "開放は2時間ごとに自動延長し、サーバー停止時・アプリ終了時に閉じます。\n"
                                 "⚠ UPnPはルーターの設定を変更する機能です。必要なときだけ使ってください。"))
        self.upnp_state = C.help_label("")
        v.addWidget(self.upnp_state)
        self.upnp_auto_close = QCheckBox("サーバー停止時・アプリ終了時に自動で閉じる (おすすめ)")
        self.upnp_auto_close.setChecked(True)
        v.addLayout(C.hbox(C.button("UPnPでポートを開放...", self.upnp_open), C.button("開放を解除", self.upnp_close),
                           C.button("ルーターを確認", self.upnp_check), self.upnp_auto_close))
        return w

    # ---- UPnP ----
    def upnp_check(self):
        from msm.publish import upnp

        def job():
            gw = upnp.find_gateway()
            return gw, upnp.external_ip(gw)

        def done(r, e):
            if e:
                self.upnp_state.setText(f"UPnP: {e}")
            else:
                gw, ip = r
                self.upnp_state.setText(privacy.text(f"UPnP対応ルーター: {gw.friendly_name or '名前なし'} / このPC: {gw.local_ip} / グローバルIP: {ip}"))

        self.upnp_state.setText("ルーターを探しています...")
        C.bg().run(job, done)

    def upnp_open(self):
        from msm.publish import upnp

        s = self.ctx.current()
        if not s:
            return
        if not C.confirm(self, f"UPnPを使ってルーターのポート {s.port} (TCP) を開放します。\n\n"
                               "・インターネットからこのPCのMinecraftサーバーに接続できるようになります\n"
                               "・自宅のグローバルIPアドレスを知っている人は誰でも接続を試せます (ホワイトリストの利用をおすすめします)\n"
                               "・ルーターの設定を変更します\n\n開放しますか？", danger=True):
            return
        cfg = dict(s.publish_config or {})
        cfg["upnp_auto_close"] = self.upnp_auto_close.isChecked()
        s.publish_config = cfg
        instance.save_server(s)

        def job():
            gw = upnp.find_gateway()
            lease = upnp.add_mapping(gw, s.port)
            return gw, lease, upnp.external_ip(gw)

        def done(r, e):
            if e:
                C.error(self, f"ポートを開放できませんでした:\n{e}\n\n手動でのポート開放の手順は上の説明を参照してください。")
                return
            gw, lease, ip = r
            msg = f"ポート {s.port} を開放しました (ルーター: {gw.friendly_name or '不明'})。\n接続先: {ip}" + ("" if s.port == 25565 else f":{s.port}")
            if lease == 0:
                msg += "\n\n⚠ このルーターは期限付きの開放に対応していないため、閉じるまで開放されたままになります。"
            self.upnp_state.setText(privacy.text(msg.replace("\n", " ")))
            cfg2 = dict(s.publish_config or {})
            cfg2["global_ip"] = ip
            s.publish_config = cfg2
            instance.save_server(s)
            C.info(self, privacy.text(msg))

        C.bg().run(job, done)

    def upnp_close(self):
        from msm.publish import upnp

        s = self.ctx.current()
        if not s:
            return

        def job():
            gw = upnp.gateway_for_remembered(s.port) or upnp.find_gateway()
            upnp.delete_mapping(gw, s.port)
            return True

        C.bg().run(job, lambda r, e: C.warn(self, f"解除できませんでした: {e}") if e else
                   (self.upnp_state.setText(f"ポート {s.port} の開放を解除しました。"), C.info(self, "解除しました。")))

    def _custom_page(self):
        w = QGroupBox("VPN・その他のトンネル")
        f = QFormLayout(w)
        f.addRow(C.help_label("Tailscale・ZeroTier・Radmin VPNなどのVPNは、各ソフトで接続したあと「接続アドレス」にVPN上のIPを入力してください。\n"
                              "ngrokなどコマンドで起動するトンネルは、実行ファイルと引数を指定するとサーバーと連動して起動・停止できます。"))
        self.c_exe = QLineEdit()
        f.addRow("実行ファイル (任意)", C.hbox(self.c_exe, C.button("参照...", self._browse_exe), stretch_end=False))
        self.c_args = QLineEdit()
        self.c_args.setPlaceholderText("例: tcp 25565")
        f.addRow("引数", self.c_args)
        self.c_addr = QLineEdit()
        self.c_eye = C.button("👁", lambda: self._toggle_secret(self.c_addr, self.c_eye), "icon")
        self.c_eye.setCheckable(True)
        f.addRow("接続アドレス", C.hbox(self.c_addr, self.c_eye, stretch_end=False))
        f.addRow(C.hbox(C.button("保存", self.save, "primary"), C.button("トンネルを起動", self.custom_start),
                        C.button("停止", self.custom_stop), C.button("アドレスをコピー", lambda: self._copy(self.c_addr.text()))))
        return w

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        s = self.ctx.current()
        self.setEnabled(s is not None)
        if not s:
            return
        cfg = s.publish_config or {}
        self.pack_card.setVisible(s.is_modded)
        self.radios.get(s.publish_method, self.radios["lan"]).setChecked(True)
        self.with_server.setChecked(bool(cfg.get("tunnel_with_server", False)))
        self.stop_with.setChecked(bool(cfg.get("stop_tunnel_with_server", True)))
        self.pl_addr.setText(cfg.get("playit_address", "") or self.ctx.playit.proc.address)
        self.c_exe.setText(cfg.get("custom_exe", ""))
        self.c_args.setText(cfg.get("custom_args", ""))
        self.c_addr.setText(cfg.get("custom_address", ""))
        for e, eye in ((self.pl_addr, self.pl_eye), (self.c_addr, self.c_eye)):
            eye.setChecked(False)
            eye.setVisible(privacy.enabled())
            self._toggle_secret(e, eye)
        addrs = T.join_addresses(s)
        self.lan_addr.set_value(addrs["lan"][0] if addrs["lan"] else "")
        self.lan_local.setText(f"同じPCから: {addrs['localhost']}" + (f"　(他のLANアドレス: {len(addrs['lan']) - 1}個)" if len(addrs["lan"]) > 1 else ""))
        if cfg.get("global_ip"):
            self.d_ip.setText(privacy.text(f"グローバルIP: {cfg['global_ip']} (前回確認)"))
        j, _r, _n = jm.resolve_for_server(s)
        self.fw_text.setPlainText(T.firewall_commands(s.port, j.path if j else ""))
        from msm.publish import upnp

        self.upnp_auto_close.setChecked(bool(cfg.get("upnp_auto_close", True)))
        rem = upnp.remembered().get(str(s.port))
        self.upnp_state.setText(f"UPnP: ポート {s.port} を開放中 (このアプリで開放)" if rem else "UPnP: このアプリでは開放していません")
        self._method_changed()
        if s.publish_method == "playit":
            self.playit_refresh()

    def _make_pack(self):
        page = getattr(self.window(), "pages", {}).get("mods")
        if page:
            page.make_client_pack()

    def _toggle_secret(self, edit, eye) -> None:
        from PySide6.QtWidgets import QLineEdit

        edit.setEchoMode(QLineEdit.Normal if (eye.isChecked() or not privacy.enabled()) else QLineEdit.Password)

    def _method(self) -> str:
        return next((k for k, r in self.radios.items() if r.isChecked()), "lan")

    def _method_changed(self, *_):
        m = self._method()
        for k, w in self.pages.items():  # 選んだ方式の説明だけ表示する
            w.setVisible(k == m)

    def save(self):
        s = self.ctx.current()
        if not s:
            return
        m = self._method()
        if m == "direct" and s.publish_method != "direct":
            if not C.confirm(self, T.METHOD_INFO["direct"]["demerits"] + "\n\n直接公開を選びますか？", danger=True):
                return
        cfg = dict(s.publish_config or {})
        cfg.update({"tunnel_with_server": self.with_server.isChecked(), "stop_tunnel_with_server": self.stop_with.isChecked(),
                    "playit_address": self.pl_addr.text().strip(), "custom_exe": self.c_exe.text().strip(),
                    "custom_args": self.c_args.text().strip(), "custom_address": self.c_addr.text().strip()})
        s.publish_method = m
        s.publish_config = cfg
        instance.save_server(s)
        self.ctx.status.emit("招待方法を保存しました")

    def _copy(self, text: str):
        if text.strip():
            C.copy_text(text.strip())
            self.ctx.status.emit("アドレスをコピーしました")


    # ---- playit ----
    def playit_refresh(self):
        def done(st, err):
            if err or st is None:
                self.pl_status.setText(f"状態を確認できません: {err}")
                return
            self.ctx.playit_status = st
            if not st.installed:
                self.pl_status.setText("playit: 未インストール\n公式サイトからダウンロードしてインストールしてください (playitを使わない場合は他の公開方法を選べます)。")
            else:
                self.pl_status.setText(f"playit: インストール済み (バージョン {st.version or '不明'})\n場所: {st.path}\n状態: {st.message}"
                                       + (f"\n自動取得したアドレス: {self.ctx.playit.proc.address}" if self.ctx.playit.proc.address else ""))
        C.bg().run(T.playit_status, done)

    def playit_from_log(self):
        s = self.ctx.current()

        def done(info, err):
            if err or info is None:
                self.pl_log.setText(f"ログを読めませんでした: {err}")
                return
            lines = []
            port = str(s.port) if s else ""
            for o in info.origin_errors:
                if port and not o.endswith(":" + port):
                    lines.append(f"⚠ playitのトンネルの転送先 {o} に接続できなかった記録があります。"
                                 f"サーバーのポートは {port} です。playit.ggのトンネル設定でローカルポートを {port} にしてください。")
                else:
                    lines.append(f"⚠ 転送先 {o} に接続できなかった記録があります (サーバーが停止していた可能性があります)。")
            if info.api_error:
                lines.append("⚠ " + info.api_error)
            if info.public_addresses:
                lines.append(f"ログに記録された公開側の接続先: {', '.join(info.public_addresses)} (最終: {info.last_seen} UTC)\n"
                             "このIP:ポートでも参加できますが、playit.ggのトンネル設定ページに表示される名前付きアドレス "
                             "(〜.joinmc.link など) を使うのがおすすめです。")
                if not self.pl_addr.text().strip() and C.confirm(
                        self, f"ログから接続先 {privacy.text(info.public_addresses[0])} が見つかりました。\n接続アドレス欄に入れますか？\n"
                              "(誰かが一度接続したときの記録です。トンネル設定を変えた場合は古い可能性があります)"):
                    self.pl_addr.setText(info.public_addresses[0])
                    self.save()
            if not lines:
                lines.append("ログに接続の記録がありません (まだ誰も接続していないか、playitがサービスとして動いていません)。"
                             "playit.ggのトンネル設定ページでアドレスを確認してください。")
            self.pl_log.setText(privacy.text("\n".join(lines)))

        C.bg().run(T.playit_log_info, done)

    def playit_start(self):
        try:
            msg = self.ctx.playit.start()
            self.ctx.status.emit(msg)
            self.ctx.playit.proc.on_exit = lambda code: self.ctx.tunnel_line.emit(f"[playit] プロセスが終了しました (終了コード {code})")
        except Exception as e:  # noqa: BLE001
            C.error(self, str(e))
        self.playit_refresh()

    def playit_stop(self):
        st = T.playit_status()
        stop_service = False
        if st.service_mode and st.service_running:
            stop_service = C.confirm(self, "playitは常駐サービスとして動いています。サービス自体も停止しますか？\n"
                                           "(「いいえ」を選ぶと、本アプリでの監視だけを終了します)")
        self.ctx.playit.stop(stop_service)
        self.playit_refresh()

    def playit_setup(self):
        st = T.playit_status()
        if not st.installed:
            C.warn(self, "playitがインストールされていません。")
            return
        if C.confirm(self, "playitの初期設定画面 (コマンドプロンプト) を開きます。\n表示されるURLをブラウザで開き、playit.ggアカウントと連携してください。"):
            subprocess.Popen(["cmd.exe", "/k", st.path], creationflags=subprocess.CREATE_NEW_CONSOLE)

    # ---- direct ----
    def check_ip(self):
        if not C.confirm(self, "外部サービス (api.ipify.org) に接続してグローバルIPアドレスを確認します。よろしいですか？"):
            return

        def done(ip, err):
            if err:
                C.error(self, f"確認できませんでした: {err}")
                return
            s = self.ctx.current()
            self.d_ip.setText(privacy.text(f"グローバルIP: {ip}" + ("" if s.port == 25565 else f"  (接続先: {ip}:{s.port})")))
            cfg = dict(s.publish_config or {})
            cfg["global_ip"] = ip
            s.publish_config = cfg
            instance.save_server(s)

        C.bg().run(T.global_ip, done)

    def local_test(self):
        s = self.ctx.current()
        if not s:
            return
        ok = ports.is_listening(s.port)
        C.info(self, f"ポート {s.port}: " + ("サーバーが待ち受けています。" if ok else "待ち受けていません (サーバーが停止中か、起動途中です)。"))

    def ext_test(self):
        s = self.ctx.current()
        ip = (s.publish_config or {}).get("global_ip") if s else ""
        if not ip:
            C.info(self, "先に「グローバルIPを確認」を押してください。")
            return
        if not C.confirm(self, f"外部サービス (api.mcsrvstat.us) から {privacy.text(ip)}:{s.port} に接続できるかテストします。\n"
                               "あなたのグローバルIPとポートがこのサービスに送信されます。よろしいですか？"):
            return

        def done(r, err):
            if err:
                C.error(self, f"テストできませんでした: {err}")
            elif r["online"]:
                C.info(self, f"外部から接続できました。\nバージョン: {r['version']}")
            else:
                C.warn(self, "外部から接続できませんでした。\nサーバーが起動しているか、ルーターのポート開放とファイアウォールの設定を確認してください。\n"
                             "(テストサービス側のキャッシュにより、設定直後は数分遅れることがあります)")

        C.bg().run(lambda: T.external_check(ip, s.port), done)

    def open_fw(self):
        subprocess.Popen(["control.exe", "/name", "Microsoft.WindowsFirewall"])

    # ---- custom ----
    def _browse_exe(self):
        p, _ = QFileDialog.getOpenFileName(self, "トンネルの実行ファイル", "", "実行ファイル (*.exe)")
        if p:
            self.c_exe.setText(p)

    def custom_start(self):
        s = self.ctx.current()
        if not s:
            return
        self.save()
        s = instance.get_server(s.id)
        try:
            msg = self.ctx.start_tunnel(s)
            self.ctx.status.emit(msg)
        except Exception as e:  # noqa: BLE001
            C.error(self, str(e))

    def custom_stop(self):
        s = self.ctx.current()
        if s and s.id in self.ctx.custom_tunnels:
            self.ctx.custom_tunnels[s.id].stop()
