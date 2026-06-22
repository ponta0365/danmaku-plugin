# OBS Danmaku OAuth Bridge

TwitchおよびYouTube Liveのコメント（弾幕）を取得し、OBS Studio上に美しく流すための「常駐トレイアプリ（Python）」および「OBSプラグイン（C++）」の連携ソフトウェアです。

---

## 1. 特徴

- **マルチプラットフォーム連携**: Twitch EventSub (WebSocket) と YouTube Data API v3 を活用し、両サイトのコメントをリアルタイムに取得します。
- **OBS Studio追従起動**: OBS Studioの起動に合わせて、トレイアプリがバックグラウンドで自動的に起動します（設定でオン/オフ可能）。
- **OBS配信連動**: OBSで「配信開始」ボタンを押すだけで自動的にコメント取得がスタートし、「配信停止」で自動的に終了します。
- **システム動作検査（Diagnostics）機能**: アプリ側でTwitch/YouTubeの認証状態、OBSの接続状態、テキスト出力ファイルの書き込み権限を自動チェックし、動作可能状態になると **ALL OK** をグリーン表示します。
- **セキュアな認証トークン保護**: 取得したアクセストークンは、Windowsのデータ保護API (DPAPI) によりハードウェアレベルで暗号化してローカル保存され、平文のままディスクに残ることはありません。
- **マルチカラー描画対応**: コメント描画は、固定色、プラットフォームカラー（Twitch:紫 / YouTube:赤）、または各ユーザーが設定しているチャットユーザーカラーを選択可能です。

---

## 2. フォルダ構成

本プロジェクトは以下の構造となっています：

- `danmaku-bridge-tray/` : コメント取得用トレイ常駐アプリケーション（Python/PySide6）
  - `src/` : ソースコード
  - `config/` : クライアント情報やローカル設定ファイル
- `danmaku-plugin/` : OBS Studio用の弾幕描画プラグイン（C++）
  - `src/` : プラグインのソースコード
  - `CMakeLists.txt` : CMakeビルド設定ファイル
- `danmaku-bridge-release/` : 配布用にビルド・クリーンアップされた配布用パッケージフォルダ
- `README.md` : 本リポジトリの説明書（本書）
- `LICENSE` : GPLv3 ライセンス

---

## 3. 開発者キー（Client ID / Secret）の取得

本アプリを個人の環境で動作させるには、ご自身用の「Client ID」および「Client Secret」を登録する必要があります。

### A. Twitch 開発者キーの取得方法
1. **Twitch開発者コンソール** ( https://dev.twitch.tv/console ) にアクセスします。
2. **【重要・必須】** 開発者登録を行うTwitchアカウントは事前に「2段階認証（2FA）」を有効化しておく必要があります。有効化されていない場合、アクセスが拒否されます。
3. 「アプリケーションの登録 (Register Your Application)」をクリックします。
4. 以下の項目を設定します：
   - 名前: 任意の名前（例: `OBS Danmaku Bridge`）
   - 連絡先Eメール: ご自身のメールアドレス
   - OAuthリダイレクトURL: `http://localhost:17563/callback`
   - カテゴリ: 「Chat Bot」または「Other」
5. 作成し、表示される「クライアントID (Client ID)」と「クライアントシークレット (Client Secret)」をアプリの「Developer Credentials」タブに登録して保存します。

### B. Google（YouTube）開発者キーの取得方法
1. **Google Cloud Console** ( https://console.cloud.google.com/ ) にアクセスし、プロジェクトを作成します。
2. 「YouTube Data API v3」を検索して有効化します。
3. 「OAuth 同意画面」を「外部 (External)」で作成し、**「テストユーザー (Test users)」にご自身の配信用のGoogleメールアドレスを必ず追加してください。**
4. 「認証情報」→「認証情報を作成」→「OAuth クライアント ID」を作成します（アプリケーションの種類は「デスクトップ アプリ」）。
5. クライアントIDおよびクライアントシークレットを取得し、アプリの「Developer Credentials」タブに登録します。

---

## 4. ライセンス

このプロジェクトは **GNU GPLv3**（GNU General Public License Version 3）ライセンスのもとで公開されています。詳細な規約は `LICENSE` ファイルをご参照ください。

---

## 5. 謝辞 (Acknowledgments)

本ツールの構築にあたり、多くの素晴らしいオープンソースライブラリやフレームワークの開発者様に心より謝意を表します。

- **OBS Studio** (obsproject)
- **Python & PySide6** (Qt)
- **requests & urllib** (Kenneth Reitz / Python コミュニティ)
- **websockets** (Aymeric Augustin)
- **pywin32** (Mark Hammond)
- **psutil** (Giampaolo Rodola)

素晴らしいオープンソースのエコシステムに感謝いたします。
