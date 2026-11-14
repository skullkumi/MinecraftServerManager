# Minecraft Server Manager - 開発者向け資料

利用者向けの説明は [docs/README.md](docs/README.md)、機能の対応状況は [docs/FEATURES.md](docs/FEATURES.md) を参照。

## 構成

```
main.py                    エントリーポイント
msm/
  __init__.py              アプリ名・バージョン
  core/                    共通基盤 (GUI非依存)
    paths.py               保存先 (%LOCALAPPDATA%\MinecraftServerManager)・ダウンロードフォルダー取得
    db.py                  SQLite (サーバー登録・Java・バックアップ履歴・MOD履歴・設定・キャッシュ)
    net.py                 HTTP (進捗・ハッシュ検証・キャンセル・一時ファイル経由の保存)
    secrets.py             DPAPIによるAPIキー暗号化
    util.py                ログ・キャンセル・サイズ計算など
  java/                    Java管理
    detect.py              検出 (JAVA_HOME/PATH/レジストリ/各ランチャー)・バージョン/bit/配布元判定
    requirements.py        Minecraft/サーバー種類ごとのJava要件
    adoptium.py            Eclipse Temurin の取得・検証・展開
    manager.py             一覧管理・サーバーへの割り当て
  server/                  サーバー管理
    providers.py           Vanilla/Paper/Fabric/Forge/NeoForge の配布元API (URLはここに集約)
    instance.py            サーバー登録情報・作成・既存フォルダー登録
    process.py             プロセス起動/停止/状態/プレイヤー検出
    properties.py          server.properties の読み書きと日本語定義
    ops.py                 Java解決→起動、安全なバックアップ
    monitor.py / players.py / presets.py / ports.py
  mods/                    MOD管理
    metadata.py            fabric.mod.json / quilt.mod.json / mods.toml / neoforge.mods.toml / mcmod.info / plugin.yml
    versionrange.py        Maven・SemVer形式のバージョン範囲判定
    classify.py            サーバー対応判定
    deps.py                依存関係・グループ化
    importer.py            パック解析・展開・取得・取り込み計画
    modrinth_api.py / curseforge_api.py
    download_watch.py      ダウンロードフォルダー監視
    disable.py             無効化・復元・履歴
  diagnose/
    analyzer.py            ログ診断ルール
    bisect.py              テスト環境・自動切り分け
  backup/backup.py         バックアップ
  publish/tunnels.py       playit・直接公開・LAN・カスタムトンネル (拡張は METHOD_INFO と Controller を追加)
  integrations/external.py CurseForge・Minecraftランチャー
  update.py                更新 (確認・SHA-256検証・入れ替え)
  mods/clientpack.py       友達用パック  /  mods/resolver.py 前提MODの自動取得  /  mods/updates.py MOD更新
  mods/classscan.py        クラス解析によるクライアント専用判定
  server/schedule.py       スケジュール判定  /  server/metrics.py 負荷記録
  publish/upnp.py          UPnP  /  integrations/discord.py Discord通知  /  core/startup.py サインイン時起動
  gui/                     PySide6 GUI (core層を呼ぶだけ。重い処理は run_task / bg で別スレッド)
    app.py / main_window.py / context.py / actions.py / common.py / pages/*.py
    automation.py (自動再起動・スケジュール・通知・負荷記録) / wizard.py / chart.py / fetch_mods.py
    theme.py (ライト/ダーク) / privacy.py (伏せ表示) / i18n.py + i18n_en.py (英語UI)
tests/
  test_unit.py ほか test_*_unit.py   単体テスト (ネットワーク不要)
  it_*.py                  追加機能の統合テスト (実サーバー・実MOD)
  eval_classscan.py        クライアント専用判定の精度確認 (実在MOD 53件)
  gui_smoke.py             全画面の生成テスト
  gui_flow.py              GUI操作の自動テスト (実サーバー使用)
  gui_smoke_all.py         全画面を日本語/英語・ライト/ダークで生成する確認
  it_multi.py              サーバー一覧の一括操作 (同時起動・ポート重複回避)
  integration_servers.py   5種類のサーバーを実際に作成・起動・停止
  integration_mods.py      実MODでの分類・取り込み・監視・診断・自動切り分け
build/
  msm.spec / version_info.txt / build.ps1 / make_icon.py
assets/icon.ico, icon.png
docs/                      配布物に入れるドキュメント
```

## 開発環境

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python main.py
```

テスト用にデータフォルダーを分ける場合は環境変数 `MSM_DATA_DIR` を指定する。

## テスト

```powershell
.venv\Scripts\python -m pytest -q tests\test_unit.py
$env:MSM_DATA_DIR="$env:TEMP\msm_test"; .venv\Scripts\python tests\integration_servers.py $env:TEMP\msm_it
.venv\Scripts\python tests\integration_mods.py $env:TEMP\msm_modtest $env:TEMP\msm_it\fabric-1.20.1
.venv\Scripts\python tests\gui_flow.py $env:TEMP\msm_it
MinecraftServerManager.exe --selftest result.json   # 配布版EXEの自己診断
```

## ビルド

```powershell
.\build\build.ps1
```
`dist\MinecraftServerManager-v<版>.zip` と `.sha256` ができる。

## 英語UIの辞書
```powershell
.venv\Scripts\python build\i18n_extract.py             # 未翻訳の表示文字列を一覧
.venv\Scripts\python build\i18n_extract.py --dump ja.tsv
# ja.tsv と同じ番号で en.tsv (番号<TAB>英語) を書き、
.venv\Scripts\python build\i18n_build.py ja.tsv en.tsv  # msm\gui\i18n_en.py に追記
```

## 配布物に含めるもの / 含めないもの

| 配布ZIPに含める | 説明 |
|---|---|
| MinecraftServerManager.exe | アプリ本体 (ポータブル版: 単体EXE / フォルダー版: _internal フォルダーと一緒に配布) |
| MinecraftServerManager-Setup-*.exe | インストーラー (Inno Setupがある場合) |
| *.sha256 / latest.json | 配布ファイルのハッシュ・アプリ更新用の情報 (配布サイトに置く) |
| README.md | 利用者向けの使い方 |
| はじめにお読みください.txt | 最初に読む短い説明 |
| FEATURES.md | 対応状況・未検証の機能 |
| CHANGELOG.md / LICENSE.txt / THIRD_PARTY_LICENSES.txt | 変更履歴・ライセンス |

| 配布しない (開発用) | 説明 |
|---|---|
| msm/ , main.py | ソースコード (EXEに同梱済み) |
| tests/ | テスト |
| build/ (msm.spec, build.ps1, installer.iss, i18n_*.py, make_icon.py, work/) | ビルド用 |
| .github/ | CI設定 |
| .venv/ , requirements*.txt | 開発環境 |
| dist/ 内のZIP以外 | ビルドの中間生成物 |
| DEVELOPER.md | この資料 |

## 設計上のルール
- 元のファイルを変更・削除しない (MODは disabled_mods へ移動、ワールドは .msm/old_worlds・.msm/replaced へ移動)
- 変更前にバックアップ (backup_file / create_backup)
- 未実装の機能を実装済みとして表示しない。取得できない値は「未対応」と表示する
- ダウンロードは一時ファイル→ハッシュ検証→リネーム。既存ファイルは上書きしない
