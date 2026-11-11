# 2026-11-11 particle-main 2秒超イベント

Issue: https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/11
基準SHA: a870833dcd50c16837b0b6352d721e48ba0d7f7b
対象run: run-20261111T013430.008955Z-particle-main
Linux offscreen/dummy音声、CPUQuota75%、Nice10。Windows元現象と同一視しない。

## 実測

- 20分完走・exit0。update最大2146.504ms、frame間隔最大2166.950ms。
  2種類のイベントは同一run・同一ピークの観測で、独立した2件の停止ではない。
- 初めて2秒超update最大がサンプルへ反映されたのはUTC01:39:57.326624
  （JST10:39:57.326624）。直前サンプルは01:39:55.061413。
- このサンプル間隔は2.265212681秒、process CPU増分は0.154954750秒。
  ピークupdateを含む区間のCPU時間であり、updateだけのCPU時間ではないが、
  「このupdateが2秒間CPUを使用し続けた」とは整合しない。
- 同区間の監視cgroup cpu.stat増分:
  usage_usec=157893、nr_periods=17、nr_throttled=0、throttled_usec=0。
  CPUQuota75%の設定は維持されているが、**当該区間をこのcgroup自身のquota消費による
  throttlingだけで説明することはできない**。祖先cgroupやスケジューラ競合は別。
- ホストCPU PSI some avg10は周辺で42.97〜49.31%。CPU使用率ではなく待ちの指標。
  01:39:55.926〜57.939のPSI some total増分は約1.474秒。
  同区間、memory PSI totalは変わらず、MemAvailableは約2.4GB、SwapFree約2.1GB。
  計測した窓でホスト全体のメモリ圧迫が主因という証拠はない。
  個別のページフォルト・IO待ち・短いGC・OS/ネイティブ内部の待ちは除外しない。
- faulthandlerに1回の2秒timeout。Python stackはinterpreter.execute:862
  → step → session.run_frame → App.update。
  execute:862は `if kind == "VAR"` という通常のdispatch分岐。
  ここでのsnapshotは「その行が2秒処理した」証拠ではない。
- 対象runの定常サンプルにqueue/スタックの持続増大はなく、正常終了した。

## 仮説の更新

|候補|評価|
|---|---|
|BASICが2秒分のCPUを使う重い単命令|当該ピーク全体の説明としては不整合。別runでの可能性は残る|
|監視cgroup自身のCPUQuota75%によるthrottlingのみ|当該ピーク窓で増分0、単独説明として否定|
|ホストCPU競合による低優先度タスクの実行待ち|CPU時間の小ささ、CPU PSI増加と整合する有力候補。原因確定ではない|
|ブロッキング・GIL・native待機・祖先制限|既存データでは区別不能|
|メモリリークによるホスト全体圧迫|当該窓のmemory PSI/availableと不整合。一般的なリーク否定ではない|

他プロセスの個人情報を採取せず、今回の証拠だけから競合元プロセスを推測しない。
ホストを故意に高負荷にする試験は実施していない。

## 次回採取の補強（診断限定・schema3）

- update前後のprocess_time/thread_timeで、500ms以上のupdateについてwall/CPU時間を保存。
  `.slow.jsonl`へ逐次flushし、JSON結果内は末尾64件だけ保持。全件数も保存。
- non_cpu_wall_msはwall−thread CPUの概数。ブロック・preemption等を含み原因ラベルではない。
  CPU時計の取得順による微小差もある。各フレームにCPU時計読取りを追加するため
  観測コストはゼロではなく、製品の性能値とは区別する。
- 各slow recordに終了時のcgroup・`/proc/thread-self/schedstat`を保存。
  定期サンプルにもschedstatを追加。runqueue_wait_ns等は累積値で、直前定期サンプルとの
  差分は複数updateを含み得る。カーネルで無効/未対応ならnullや0となる場合がある。
- 既存の全期間max/count・4096件保持・異常閾値・20分8ケースは維持。
  monitor/judge・サービス設定は変更していない。実行中の子はそのまま完走し、次の子から適用。

## 検証

- 診断関連unittest 10件成功（3.018秒、tests.txt）。
- `check_slow_update.py` が実Appの初回updateだけに600ms sleepを注入する診断fixture。
  別の一時systemd unit（CPUQuota75%、MemoryMax192M）で3秒実行・exit0。
  更新後のscheduler-check.jsonではwall=600.363ms、thread CPU=0.307ms、
  non-CPU wall>500msを記録し、schedstat/cgroupも採取できた。
  人工sleepは計測の動作確認であり、元イベントの再現・原因証明ではない。
- テストfixture以外の製品コードは変更していない。上流PRなし。
- Issue11のLOOP Leaseはhashキー/旧番号キーとも存在せず。ソースレビュー#90は独立worktreeの
  完了記録を確認、当該レビューbranchへ変更していない。
- 監視PID1515922と稼働中particle-thread子1678268を維持、service/timerはactive。

## 前回メモリ仮説の追加観測

最近の完走schema2 runをケースごとに1本取り、60〜120秒と1080〜1140秒のRSS中央値を比較。
idle +2.54MiB、録画なしidle +2.53MiB、audio main/thread +2.50/+2.55MiB、
cube +2.51MiB、particle-thread +2.57MiB、puzzle +3.79MiB、particle-main −11.10MiB。
`recent-rss.json`と各`.rss.json.gz`参照。初回全保持より小さい傾向だが、時刻・負荷が異なり、
前回は60秒値と終了値、今回は窓内中央値という集計差もある。厳密な因果比較ではない。
診断器samples/cgroupの毎秒割当が残るため一定量の増加は想定されるが、全量説明はしない。

## 次の検証

1. 次の2秒超イベントで、個別updateのwall/thread CPUとschedstat差分を照合。
   runqueue待ち増ならスケジューラ競合、待ち増が乏しければブロック/ネイティブ等を絞る。
2. 必要時だけ同一条件のCPU quota/Nice比較を直列に行い、ホスト負荷を記録して解釈する。
   今回の証拠からquotaを解除したり、常駐優先度を上げたりはしない。
3. メモリの残存増は毎秒サンプル割当やアロケータ保持、録画なし/thread等で比較継続。
4. Windows証拠待ちで停止せず、現行サービスを継続する。

## 証拠の保存

元runのresult、samples、host、meta、anomaly、process.logをgzip保存。
sha256.jsonは圧縮前の元ファイルhash。peak-window.jsonはサンプル/ホストの該当窓と差分。
recent-rss.jsonには元result hash、各rss gzipにはメモリ時系列のみを保存する。
