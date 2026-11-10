# PC全体が一時的に重くなる現象の調査

## 現在の運用（2026-11-10）

Takeの指示により、Windows証拠待ちで全調査を止める方式を廃止。
常時スクリプト計測と証拠トリガーのLLM判断へ移行した。
[継続調査の運用](CONTINUOUS-STALL-INVESTIGATION.ja.md)を参照。
以下の11/9実測・Windows採取手順は過去成果として維持する。

## 状態と作業範囲（2026-11-09）

- 管理Issue: https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/11
- upstream: `TakeshiMaeda/PyxelBasic`、調査fork: `crowclaw2026/PyxelBasic`
- 基準commit: `be031fd`（v0.2.1）。製品コードは変更していない。
- **元のWindows上の数秒間のPC全体の停滞は原因未特定。** Linuxでの短時間の試験を「問題なし」「再現不能と確定」の根拠にはしない。
- 本家へのpush・PR提出・報告者への連絡は行っていない。

## 既存の診断手段

独自のプロファイラや監視サービスは導入せず、Python標準の `perf_counter`、
`process_time`、`faulthandler`、Linuxの `/proc/self/statm` を利用した。
Windows版のWorking Set取得は標準の `GetProcessMemoryInfo` を呼ぶ。
OS全体の停滞はPythonだけでは特定できないため、必要時はWindows標準のWPRを使う。

`tools/diagnose_stall.py` は既存 `App.update/draw` を包む有限時間の起動器であり、
別実装のVMや描画モックで速度を測ってはいない。UIの入力操作も通常どおり可能。
2秒以上フレームが進まない場合はPythonの全スレッドのスタックを標準エラーへ出す。
正常なフレームごとにタイマーを再設定し、正常待機中の誤ったタイムアウト出力を避ける。
計時・タイマー設定・サンプル取得自体のオーバーヘッドは含まれる。

## コード上の候補と確認範囲

| 候補 | コードの根拠 | 今回わかったこと／残る条件 |
|---|---|---|
| mainモードの長いフレーム | `session.py:370` の `run_frame` は既定8000命令で区切るが時間上限なし。`app.py:165` から同期実行 | CPUループで長いフレームを実測。命令単体の重さでも時間は変わる。PC全体の数秒停止とは同一視しない |
| threadモードの過密ループ | `session.py:271` は既定400命令／64msで待機。周期0ではEDIT/INPUT中もsleepしない | 既定と待機なしを比較。待機なしは意図的な高負荷設定であり、自動修正しない |
| 描画・音声キューの無限滞留 | `runtime.py:174` 以降。通常putは容量1024でCondition待機、drainはロック内で全件実行 | 単一VM producerのputは上限で止まる。同期call/pgetは容量チェック外だがproducer自身が応答待ちするため、通常経路で無制限投入し続ける形ではない。個々の描画・音声処理が遅ければmain側を止め得る |
| 配列・文字列・制御スタックの増大 | `interpreter.py:807` のDIM、`:1018` のGOSUB、`:1091` 以降のFOR。一般的な資源量上限なし | DIMと上限10万回のGOSUBケースを実行。BASICプログラム由来の増大はキュー増大と別物。`reset_runtime` は次のRUNで配列・スタックを初期化する。無制限なメモリ圧迫試験はしていない |
| 文字入力の蓄積 | `runtime.py:42` 以降の入力リング・typeahead | 各256件に制限済み。既存テストにも上限の検証がある。入力しない試験だけで他のメモリ問題は否定しない |
| 初期RSSの増加 | `app.py:44` のPyxel初期化では録画設定を上書きしていない | 既定の10秒録画リングと整合。録画0秒の対照実験と上流ソースで確認。下記参照 |
| 音声／画面ドライバ・ネイティブロック | `audio.py` は毎回Soundを生成しMML解析後に再生。`PLAY`はmain側で処理 | ダミー音声での試験はWindowsの実デバイス、DPC/ISR、GPU、長時間のBGM割込み復帰を検証しない |

ソース位置は基準commitのもの。特定のプログラム・デバイスとの相関はまだ不明。
再現しない条件から原因候補を網羅的に否定したわけではない。

## Crow機の計測条件

- Ubuntu 24.04系、Linux `7.0.0-38-generic`、Intel Core i7-2620M（2コア4スレッド）、RAM約8GiB。
- Python 3.12.3、Pyxel **2.9.5**。元の報告環境のPython/Pyxelバージョンは不明。
- `SDL_VIDEODRIVER=offscreen`、`SDL_AUDIODRIVER=dummy`。本物のPyxel VM／画像／音声APIを使用するが、物理画面・音声デバイスは不使用。
- 各ケースを別プロセスで順番に起動し、試験プロセス同士を並走させていない。Crow本体など既存の常駐処理は停止していない。
- 既定の画面256x256、60fps。CPU%はプロセスCPU秒／経過秒×100（**100%=論理CPU1個分**、ホスト全体のCPU%ではない）。
- RSSは現在値。最初のサンプルは初期化後なので、起動時全体のメモリ増分ではない。
- 実測JSON: [`diagnostics/loop-11/measured/`](../diagnostics/loop-11/measured/)。同名のlogに標準出力・標準エラーを保存。

### 実測結果

結果表は同ディレクトリの `SUMMARY.ja.md` を参照。

初期30秒のidleではRSSが約94MiBから約130MiBへ増え、10秒以降はほぼ横ばい。
録画0秒では約95MiBのまま。Pyxel 2.9.5の
[`Screencast::new/capture`](https://github.com/kitao/pyxel/blob/v2.9.5/crates/pyxel-core/src/screencast.rs)
は `fps*capture_sec` 枚のリングを保持し、満杯になると既存バッファを再利用する。
256×256×600フレームの画素だけで37.5MiBとなり、この実測と整合する。
この起動直後の増加だけをメモリリークの証拠にはしない。録画を無効にする製品変更は加えていない。

`cpu.bas` は入力・描画・VSYNCのない代入/GOTOループ。
既定mainでは1回の更新が最大約169ms、800命令では最大約19msだった。
これはUIスレッド占有が起こり得る根拠であり、元の数秒停止の再現ではない。
`stack.bas` は10万到達で終了する診断ケースであり、通常のサンプルではない。
`dim.bas` の配列要素数は200001に限定している。

既存テスト `tests/test_core.py`: **300 passed, 0 failed**（Pyxel 2.9.5環境）。
最初はPyxel未導入でimportに失敗したが、隔離環境へ依存を導入した後に再実行して全件成功。
Crow機はpip/ensurepip未導入のため、PyPIの公式wheel
`pyxel-2.9.5-cp310-abi3-manylinux_2_28_x86_64.whl` をSHA-256検証して
無視対象の `.venv` 配下だけへ展開した。検証値:
`e3095126593819bba73a45d7fffa66a172c864e950be8a04cf452d93025752cd`。
OSのPython・パッケージ・サービス設定は変更していない。

## 再実行

通常のPython仮想環境とPyxelがあることを前提とする。既存環境のバージョンを先に記録し、
元の現象を調べるときは勝手にバージョン変更しない。以下はリポジトリ直下から実行する。
結果ファイルは新規名を使う（既存ファイルを上書きしない）。
最終版の起動器は同じ場所へUTC時刻付きの `*.samples.jsonl` も逐次保存する。
強制終了・ネイティブ終了時に最終JSONが空でも、直前のサンプルはJSONLへ残る。
JSONL・スタックログも結果と一緒に保持する。

Linux（オフスクリーン試験）:

```bash
mkdir -p diagnostics/local
SDL_VIDEODRIVER=offscreen SDL_AUDIODRIVER=dummy timeout 140 .venv/bin/python tools/diagnose_stall.py --seconds 120 --output diagnostics/local/particle-main.json -- --load particle --run
SDL_VIDEODRIVER=offscreen SDL_AUDIODRIVER=dummy timeout 40 .venv/bin/python tools/diagnose_stall.py --seconds 30 --output diagnostics/local/cpu-main.json -- --workdir tools/stall_cases --load cpu --run
```

`--capture-sec 0` は起動器側の `--` より前に置く診断専用の比較条件。
その他は `main.py --help` と同じ引数。thread比較は `--` より後に `--exec-mode thread` を付ける。
表示されるFPSではなく、JSONのupdate/draw/frame_intervalを比較する。
メモリ比較は録画リングが埋まる10秒以降の推移を見る。

### Windowsで必要になったときの最小採取手順（実機未検証）

いま再現操作を繰り返すことや、利用者全員へ質問することは要求しない。
**次に症状が出たときに、当時のBASICプログラム、起動引数、Python/Pyxelバージョン、
発生時刻を1組残す**ことが最初の再開条件。それがあれば同一条件の再現試験へ進める。
Windows端末へこのWorkerから接続・操作は行っていない。

診断起動で元と同じプログラムを使う場合（PowerShell。`python`は普段の実行環境）:

```powershell
New-Item -ItemType Directory -Force diagnostics/local
python --version
python -c "import pyxel; print(pyxel.VERSION)"
python tools/diagnose_stall.py --seconds 120 --output diagnostics/local/windows-main.json -- --load particle --run 2> diagnostics/local/windows-main-stacks.log
```

`particle`は例。実際に症状のあったプログラムと起動引数へ置き換える。
thread比較や `--steps-per-frame 800` は元条件を採取した**後**に1条件ずつ試す。
動作速度やVSYNCの意味が変わるため、回避策として恒久適用しない。
停止期限は次のupdateが戻ってから判定するため、ネイティブ処理が固まれば自動終了しない。
その場合は別の端末から当該診断プロセスだけを終了し、スタックログを保存する。

アプリ外も重くなり、Pythonスタックだけで説明できないときに限り、
管理者端末でWindows標準WPRの短い循環記録を併用する。
他の計測中セッションを止めないよう、最初に状態を確認する。

```powershell
wpr -status
wpr -start GeneralProfile
# 症状発生直後に停止・保存（長時間のfilemode記録は使用しない）
wpr -stop diagnostics/local/windows-stall.etl
```

組込みプロファイル名・使用可能な機能は `wpr -profiles` と `wpr -help` で確認する。
コマンド根拠は[MicrosoftのWPRリファレンス](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/wpr-command-line-options)。
ETLは他アプリの情報を含み得るためローカルで保管し、自動でIssueやforkへ添付しない。
WPAで発生時刻のCPU（特定スレッド／全コア）、DPC/ISR、ディスク・ハードフォールト、
スレッドの待機先を確認し、単なるVMの長いフレームとOS側の停滞を分ける。

## 次の開始点

1. 上記の1組の実発生条件、またはWindows上の診断JSON/スタックが得られたら同条件を再現する。
2. main updateだけが遅ければ命令別の時間を追加計測し、時間予算・重い単命令・プログラム資源量のどれかを絞る。
3. app外も重い／updateとdrawが短いのにフレーム間隔だけ長い場合はOSトレースを優先する。
4. 音声操作との相関が出た場合は実音声デバイスとBGM割込み復帰を個別計測する。現在のダミー音声だけでは除外しない。
5. 原因・再現・既存動作との互換性が確認できてから修正案を用意し、Takeのレビューへ返す。上流PRは現時点の委任範囲外。
