# Minecraft Server Manager

Minecraft Java版のサーバーを、ボタン操作だけで作成・管理・MOD導入・トラブル診断・友達の招待までできる Windows 用アプリです。
CurseForge や playit.gg が無くても使えます。

- 対応サーバー: Vanilla / Paper / Purpur / Fabric / Quilt / Forge / NeoForge
- 複数サーバーの一括管理 (同時起動・一括停止・ポート重複の自動回避)
- Java の自動判定・導入 (Eclipse Temurin、環境変数を変更しない)
- MODパック取り込み (CurseForge ZIP / Modrinth .mrpack / サーバーパック)、前提MODの自動取得、友達用パックの作成
- ログからの原因推定・依存関係を保ったMODの自動切り分け
- 招待方法: LAN / playit.gg / ポート開放 (UPnP対応) / VPN・その他のトンネル
- 自動再起動・スケジュール・差分バックアップ・Discord通知・負荷グラフ
- ダークテーマ・英語UI・配信向けの伏せ表示

## ドキュメント

| 対象 | ファイル |
|---|---|
| 使う人向けの説明 | [docs/README.md](docs/README.md) |
| 機能の対応状況・未検証の項目 | [docs/FEATURES.md](docs/FEATURES.md) |
| 変更履歴 | [docs/CHANGELOG.md](docs/CHANGELOG.md) |
| 開発者向け (構成・テスト・ビルド) | [DEVELOPER.md](DEVELOPER.md) |

## 開発環境での起動

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python main.py
```

配布物 (EXE・ZIP) の作成は `.\build\build.ps1` を実行します。

## ライセンス

MIT License ([docs/LICENSE.txt](docs/LICENSE.txt))。
本ソフトウェアは Mojang Studios / Microsoft、CurseForge、playit.gg、Modrinth とは無関係の非公式ツールです。
