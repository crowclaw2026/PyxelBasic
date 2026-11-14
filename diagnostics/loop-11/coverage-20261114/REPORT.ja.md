# 2026-11-14 coverageレビュー（completed=297）

Issue: https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/11
基準SHA: 1e47beb4faea238225eafb9a80f353d6e485cb19
Linux offscreen/dummy音声・CPUQuota75%・Nice10、Windows元現象と区別する。

## 同期watchdogの長時間確認

保持中24正常runを精査。全runは20分以上・exit0、stream方式で計測中sample保持0件。
うちschema6/Python watchdogは全8ケースを含む10件（計200分）、puzzleは1件20分。
VM/watchdog thread残存なし、結果JSON samplesとJSONLが10件すべて全行一致。
親PID1515922は継続し、現在はparticle-main子1707837が稼働している。

|schema6 case|run|update最大ms|frame最大ms|late RSS差bytes|
|---|---|---:|---:|---:|
|idle-no-capture|run-20261114T005327.152055Z-idle-no-capture|5.737|53.493|0|
|idle|run-20261114T011331.319751Z-idle|7.095|115.245|0|
|particle-main|run-20261114T013335.476084Z-particle-main|855.398|881.856|0|
|particle-thread|run-20261114T015339.207553Z-particle-thread|5.351|83.228|4096|
|audio-cycle-main|run-20261114T021343.612235Z-audio-cycle-main|4.817|140.860|0|
|audio-cycle-thread|run-20261114T023347.846488Z-audio-cycle-thread|18.728|50.163|0|
|puzzle-main|run-20261114T025352.074424Z-puzzle-main|537.437|548.915|0|
|cube-main|run-20261114T031356.310344Z-cube-main|44.946|50.810|0|
|idle-no-capture|run-20261114T033400.564749Z-idle-no-capture|4.607|34.450|0|
|idle|run-20261114T035404.757058Z-idle|6.955|55.612|0|

今回確認した範囲に新SIGSEGV・新2秒閾値超過なし。以前のnative watchdogでのpuzzle SIGSEGVは
既報の同一runであり再発として数えない。現在残る異常runは既報の2秒particleとSIGSEGV puzzle。

**schema6の10runで自然発生のPython watchdog timeout dumpは0回。**
長時間起動・終了回収が正常という証拠であり、元SIGSEGVが起きたtimeout採取場面を
実負荷下で繰り返し通過した検証ではない。前回の人工2.3秒sleep試験とは区別する。
一度のSIGSEGVと200分の非再発からnative timer原因確定・修正済みと判断しない。
CPython/Pyxel/SDL等の候補やWindows元現象は未確定のまま残す。

## stream方式の20分RSSと最終化負荷

schema5/6の24runについて900〜960秒と1080〜1140秒のRSS中央値を比較。
両窓は全runでdraw600件以上・3時間系列各4096件の代理充填条件を満たすことを確認。
これは録画内部枚数・native全割当が一定という証明ではない。

- 24runの後半RSS差は0〜8192 bytes。
- 終了時のsample読戻しは0.026285〜0.108671秒。
- 最終計測sample→読戻し後RSSの差は約5.480〜5.691MiB。

計測中にsample全件を保持しないことで、旧方式の毎秒保持による増加を分離できたという
短時間tracemallocの所見と整合する。旧方式との同時刻・同負荷のランダム化比較ではないため、
RSS改善全量の因果説明やnativeリーク否定とはしない。終了時に保持を再構築する負荷が残ることも明示する。

## 判定・次の開始点

- 今回は診断・製品コードやケースの変更不要。既存sourceのwatchdog close/joinと
  sample最終化順序を確認し、実稼働のmetadata/JSONLと照合した。
- 新しい実負荷timeoutやSIGSEGVが出たらbackend・時刻・元証拠を区別し、対象の新規子に限った
  GDB比較でC stackを取りに行く。過去faultを推測で修正済みにしない。
- Linux安定/Windows待ちで監視を止めず、同じsystemd計測を継続。
- 正常な同傾向の反復では追加の比較試験やコード変更を毎回行う必要はなく、
  後続reviewは同指標を簡潔に記録する。重要な変化・未検証条件がある場合だけ深掘りする。
- 今回のコード変更なし。service/timer・起動制限・稼働中子へのsignal・上流PR/製品修正なし。

## 証拠

summary.jsonは保持中正常24件の指標。各runのresult/normal/metaをgzip/hash付き保存。
schema6の10件はJSONL/プロセスlogも保存し、結果samplesとの完全一致を照合済み。
verification.jsonのhash件数はgzip展開後の元データを対象に検証した数。
過去SIGSEGV原証拠は前commitのsegfault-20261114/originalに固定保存済みで、今回複製しない。
