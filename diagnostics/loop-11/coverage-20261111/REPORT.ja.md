# 2026-11-11 coverageレビュー（完了数80時点）

継続調査Issue: https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/11
基準SHA: 929f15d4867e5a6762451a8cdaacc2e34c84c7c6
Linux offscreen / dummy音声 / CPUQuota75% / Nice10。
Windowsの元現象の再現・原因確定ではない。

## 稼働と範囲

- completed=80を確認。これはサービス状態の累積値で、全80runを今回実読したという意味ではない。
- 保存上限内の最新完走runを各ケース1件、計8件を精査・保存。
- schema3はaudio main/thread、puzzle、cube、録画なしidleの5件で各1200秒完走。
  idle/particle main/threadの最新完走分はまだschema2。新schema3 idleは実行中。
- schema3の5件は全exit0、2秒閾値の新規超過なし。
  旧particle-mainの2秒イベントは前回929f15dで分析済みであり再発の新証拠として数えない。
- 監視PID1515922、実行中idle子2056146を維持。監視service/timerはactive。
  有効Lease JSONは0件、他Workerとの同時ソース変更なし。

|case|schema|update最大ms|frame最大ms|平均CPU（1論理CPU比）%|500ms超update|
|---|---:|---:|---:|---:|---:|
|audio-cycle-main|3|5.064|33.414|13.85|0|
|audio-cycle-thread|3|5.434|58.571|21.70|0|
|cube-main|3|27.900|39.446|69.75|0|
|idle|2|4.725|32.220|12.67|未採取|
|idle-no-capture|3|4.744|32.616|12.65|0|
|particle-main|2|2146.504|2166.950|71.40|未採取|
|particle-thread|2|32.015|427.927|47.39|未採取|
|puzzle-main|3|1128.512|1158.021|72.59|32|

## 新しい証拠：CPUを使わない時間とrunqueue待ち

puzzle-mainの20分runで500ms超updateが32回。最長update1128.512msに対し
thread CPU176.353ms、non-CPU wall952.159ms。32件でthread CPU最大182.629ms。
したがって、観測した長いupdateの壁時計時間はCPU実行時間だけでは説明できない。

例としてJST12:02:53.042終了のupdate（wall1059.662ms / thread CPU180.579ms）:

- 直前定期サンプル→slow終了の窓は1206.371ms。このうちupdate開始前146.709msを含む。
- schedstat差分: main thread実行297.541ms、runqueue待ち900.110ms。
- 同窓の監視cgroup: throttled_usec増分34374（34.374ms）、nr_throttled増分2。
- **窓全体ではrunnableなmain threadの実行待ちが大きいことを新たに確認。**
  update単独の900ms待ちと読み替えない。窓には直前のupdate/draw/計測処理を含む。
  各カウンタ読取り境界は完全同時ではないため微小誤差も含む。
- このcgroup自身のquota待ちだけで窓の待ち全量を説明するのは困難。
  CPU競合・祖先制限等は残る。blockingだけで全量説明する仮説もrunqueue待ち増加とは整合しない。
  別のブロッキングやGIL/native待機の併存までは否定しない。
- `/proc/thread-self/schedstat`は累積カウンタ。non_cpu_wall_msはCPU未実行時間の概数で原因名ではない。
  Linux制限下の所見であり、Windowsで同じ理由としない。

`slow-windows.json`は32件それぞれと直前定期サンプルを対応させたデータ。
`sched_window_ms`はmsへ換算済み。元のnsカウンタは各slow record内に保存。

## 継続計測で残す仮説

- mainの `App.update -> session.run_frame(max_steps)` は命令数制限であって時間上限ではない。
  CPU実行約180ms級の処理自体に加え、競合による実行待ちでwallが伸びる可能性がある。
- 前回particleの2秒超を今回のpuzzleと同じ原因と断定しない。
  particleのschema3が次周で得られたらupdate CPU/runqueue/制限の対応を確認する。
- メモリ増や音声dummyの安定は実デバイスの問題を除外しない。
- 今回は計測器/製品コードやサービス設定を変更せず、既存の診断起動器とsystemd一時unitで追加比較。
  新しい常駐ケースや依存ライブラリを導入する必要はない。

## 追加検証：puzzle main/thread各30秒

既存診断スクリプトを別の一時systemd unitで直列実行。
両方CPUQuota75%・Nice10・MemoryMax192M・offscreen/dummy音声、録画0秒。
常駐の録画既定ありrunとは別条件。起動引数は各command.json、実測はcomparison.jsonと生JSON/JSONL。

|mode|elapsed秒|平均CPU%|update中央値ms|update最大ms|frame中央値ms|frame最大ms|
|---|---:|---:|---:|---:|---:|---:|
|main|30.143|74.97|113.820|244.699|118.607|250.780|
|thread|30.001|26.92|0.102|5.354|16.406|88.012|

両方exit0、30秒以上、BASICエラーなし、VM thread残存なし、cpu.max=75000 100000を確認。
thread側でもBASICはRUNのまま進行し、GFX queueの1秒概数は最大12（mainは0）。
この測定は2秒停止を再現していない。

**同じ演算量/同じBASIC進行速度での比較ではない。** `session.py:41-42` のthread既定は
400命令/64msサイクル、mainは既定8000命令/update（同file STEPS_PER_FRAME、App.update呼出し）。
UIのupdateからVMを外すことに加え、このスロットル設定差もCPU/進行速度へ影響する。
「threadなら同じ仕事が速い」「Windows問題が直る」「既定をthreadへ変えるべき」とは結論しない。
短時間・固定順・同時刻でない比較であり、順序/ホスト負荷の交絡もある。
将来実行方式を比較するなら、まずBASIC命令数/描画サイクル数など進行量の指標を揃える。

## 次の開始点

1. particle/idleのschema3完走データと次の2秒イベントを待ちながら常駐計測を継続。
2. scheduler実行待ちとCPU quota/祖先制限・blockingの併存を時系列で切り分ける。
3. ケース追加が必要ならpuzzle thread/録画なし長時間を検討するが、今回常駐8ケースは変更していない。
   今回の短時間thread試験だけで負荷同等の長時間比較を済ませたとしない。
4. ソース/診断コードの変更や追加テスト実装は今回不要。既存起動器の実App2ケースと証拠整合性を検証した。
   製品修正の確定・上流PRなし。Windows待ちを理由に調査停止しない。

## 保存証拠の整合性

各runディレクトリのsha256.jsonはgzip展開後の元ファイルhash。
最新8runのresult/samples/slow/host/meta/log/normalまたはanomalyを保存。
8runのsha256一覧にある全ファイルを再展開して照合済み。
