# 2026-11-14 puzzle子SIGSEGVの調査

Issue: https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/11
基準SHA: 26b43ff7355cb0cf50712c3822fe781470fd8e00
Linux offscreen/dummy音声・CPUQuota75%・Nice10。Windows元現象とは別の観測。

## 障害の事実

- 対象run: run-20261114T002834.507471Z-puzzle-main
- 開始JST09:28:34、監視がJST09:33:20にreturncode=-11（SIGSEGV）/missing-resultを記録。
  2種類のイベントは同じ子の障害であり、別々の2クラッシュではない。
- result.jsonは開始時の空ファイルのまま。毎秒JSONLは263件、最後は経過281.413秒。
  最後のRSS137699328 bytes（約131.32MiB）、update最大397.463ms、frame最大403.421ms。
  gfx/audio queueは0、GOSUB/FOR深さ0。最後の正常sample以降の瞬間状態は不明。
- 1200秒の終了処理には達しておらず、schema5の結果用JSONL読戻しはまだ走っていない。
  「終了時の読戻しが今回直接segfaultした」という説明はタイミングと不整合。
  ただし計測器/Pyxel/CPython等による別のメモリ破損を否定した意味ではない。
- process.logはnative watchdogの2秒timeout dump中に `File ` で途切れる。
  最初のframeは`line 277 in parse_add`だが、このcheckoutのparse_addの行範囲外。
  peek:277等は有効行。コード行/関数の組み合わせが一部不整合で、停止箇所として信頼できない。
- 監視側はSIGTERM/SIGKILLでの制限停止種別を記録しておらず、RSS制限384MiBより十分小さい。
  これはプロセス内native faultの記録で、WindowsのPC全体停止と同一視しない。
- /var/crashとsystemd coredump保存先にcoreなし。対象時刻kernel journalのpython segfault照合も記録なし。
  元プロセスのC stackがないためnative fault発生位置は確定できない。

## 仮説の評価

- 計測器native timeout watchdogによるframe競合が候補。
  CPython #140815はGILを持たないtimer threadのstack採取中のSEGVを報告し、#140921は
  invalid/freed frame/codeへの対処を追加している。
  https://github.com/python/cpython/issues/140815
  https://github.com/python/cpython/pull/140921
- 同Issueの試験対象はmain branch。当機はUbuntu Python3.12.3-1ubuntu0.17。
  同一bugと断定せず、当該packageへ修正がbackport済みかもこの調査では確定しない。
- 10秒の純Python frame churn＋100us native timerをGDBで実行しexit0。
  Pyxel/SDL/音声/JSON読戻しなしの短い試験では再現せず、rare raceは否定できない。
  native_watchdog_probe.pyとnative-watchdog-probe.log参照。元faultの再現コードとは呼ばない。
- 他の候補はCPython/Pyxel/SDL等のnative状態、音声/描画側の競合、メモリ破損等。
  切れたPython dumpだけからBASICの演算子/配列/行660を根本原因と決めない。

## 診断限定の切分け変更（schema6）

- 既定`--watchdog python`: Event.wait(.25秒)するdaemon Python threadがupdate進捗を監視し、
  2秒以上進まない場合、Pythonスレッドから同期faulthandler.dump_traceback()を呼ぶ。
  旧GILなしnative timerを毎frame再armする経路から分離する。
- 比較専用に`--watchdog native`（旧経路）、`--watchdog off`を残す。
  実SIGSEGV等に対するfaulthandler.enable()は継続。
- Python watcherはGIL取得が必要。native codeがGILを保持して停止した場合には動けない。
  外側の15秒sample期限・試験期限・RSS制限は維持し、監視停止の代わりにはしない。
- 診断器のタイミング/スレッド構成が変わるため、schema5以前との性能値を同条件と解釈しない。
  watchdog_backend/thread_aliveを結果へ記録する。今回の元SIGSEGVが修正されたとは主張しない。

資料: Python3.12 faulthandler docsの同期dumpとtimer thread仕様
https://docs.python.org/3.12/library/faulthandler.html

## 検証

- 診断関連11テスト成功（3.051秒）。新watchdogのtimeout採取・停止/回収も確認。
- 実Appへ初回2.3秒sleepだけを注入し、CPU75%制限下で4秒実行・exit0。
  Python watchdogのstack採取、update最大2300.332ms、watchdog thread回収、
  JSONLと結果samplesの全件一致を確認。人工sleepは元SIGSEGV/Windows現象の再現ではない。
- 通常puzzle＋新watchdogをGDBで短時間確認する。結果は追記欄参照。

## 継続と次の検証

- 有効Leaseなし。監視親1515922は稼働継続、障害後すでにcube子1630786へ自動で進んでいる。
  このトリガーで停止・再起動・systemd設定変更・稼働中子へのsignalは行わない。
  次の子からschema6を使う。新方式の長時間での非再発はまだ未確認。
- 元faultの証拠はoriginal/へgzip/hash付きで固定保存。欠損resultもゼロbytesとして保存。
- 次回もSIGSEGVならbackendを区別し、GDBのC stackを取得する同条件の限定再試験を行う。
  他プロセスへattachせず、対象の新規子だけをGDB配下で起動する。
- Python/SDL/Pyxel更新やglobal core設定は変更しない。必要な比較は独立環境/試験条件へ分ける。
- 原因不明/Windows待ちで監視を停止しない。製品ソース修正・上流PR提出なし。

## GDB短時間puzzleの結果

新watchdogで60.102秒、GDB上のinferiorは正常終了。VM/watchdog thread残存なし、
BASICエラーなし。update最大302.912ms。元faultは約281秒であり、
60秒非再発で修正済み・observer原因確定とはしない。puzzle-python-watchdog.json/log.gz参照。
