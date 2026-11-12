# 2026-11-12 coverageレビュー（completed=152）

対象Issue: https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/11
基準SHA: aa41dfe6ae3a827251ee135b366267e34f316b00
Linux offscreen/dummy音声・CPUQuota75%・Nice10。Windows元現象とは区別する。

## 確認範囲

保持中の正常run24件（各ケース3周、各1200秒、すべてschema3/exit0）を精査。
新しい2秒閾値超過なし。過去のparticle異常runは旧schema2であり再発と数えない。
累積152runすべてを今回再解析したという意味ではない。
各ケース最新1件の生証拠をgzip/hash付き保存、全24件の指標はretained-normal-summary.json。

|case（最新）|update最大ms|frame最大ms|slow件数|RSS窓中央値差MiB|
|---|---:|---:|---:|---:|
|idle|6.909|141.753|0|2.820|
|particle-main|230.664|250.994|0|3.025|
|particle-thread|5.366|309.723|0|2.801|
|audio-cycle-main|17.049|33.642|0|2.801|
|audio-cycle-thread|5.438|122.902|0|2.816|
|puzzle-main|1338.290|1415.778|30|5.439|
|cube-main|26.404|38.480|0|2.848|
|idle-no-capture|3.860|33.035|0|2.805|

RSS差は60〜120秒と1080〜1140秒の中央値差。60秒時点でwarm-upが済む保証はない。
particle main/threadは各3runで500ms以上updateなし。前回2秒現象の原因を否定した意味ではない。
puzzleには計34slow update。最新最大1338.290ms / thread CPU159.554ms。
対応する前サンプル→slow終了の窓は1452.421ms（update前114.131msを含む）、
schedstat差分はruntime247.306ms/runqueue待ち1264.635ms、cgroup throttle21.454ms。
これも実行待ちを伴う所見だが、schedstat合計は窓の壁時計を超える。
**カウンタはupdate終了後に順次読み取られるため読取りまでの待ちも含み、境界が一致していない。**
これらをupdate単独の待ち時間に読み替えたり、合計を厳密な時間配分に使ったりしない。
従来のCPU未実行時間の所見は維持するが、blocking/競合/祖先制限の同定は未完了。

## Python割当の追加検証（診断fixture、通常性能値ではない）

標準tracemallocを使い、別の一時systemd unitでidle/puzzleを各80秒直列実行。
CPUQuota75%・Nice10・MemoryMax256M・offscreen/dummy音声・録画0秒。
20秒頃と75秒頃のsnapshotを比較。両方exit0、BASICエラーなし、VM thread残存なし。
tracemalloc自体のCPU/RSS影響、snapshot停止、短時間・固定順の交絡を含む。
Pyxel/Rust/SDLのnative割当全体は追跡できず、リーク否定やRSS全量説明には使わない。
fixtureはApp.runを一時ラップするだけで製品ソースは変更しない。

|case|比較区間秒|Python正味増bytes|上位30正増のうち診断器直接行bytes|80秒update件数|
|---|---:|---:|---:|---:|
|idle|54.984|433328|410451|4786|
|puzzle|55.021|160375|139425|311|

上位に診断器の時間系列deque・フレーム時間float・毎秒samples/cgroupの割当が現れた。
行番号は実行時の基準aa41dfeを指す（このcommitで後から追加した行とはずれる）。
idleの時間保持は20秒時点では4096件に達しておらず、20→75秒の増加に初期充填も含む。
puzzleの更新数はtracingで低下しており、両ケース同じ進行量での比較ではない。
診断器が残存Python割当へ寄与する根拠であって、正常20分RSS増の全量説明ではない。

## warm-up条件の修正

通常puzzleの最新runのRSSは約60秒119.85MiB→約120秒131.81MiB。
既存資料にある「録画リングが埋まる10秒以降」を全ケースへ一般化するのは不適切だった。
Pyxel2.9.5 Screencastはfps*capture_sec個の枠を用意し、capture呼出しごとに枠を埋め再利用する。
設定60fps/10秒なら600枠であり、低FPS時の壁時計10秒/固定60秒では充填を保証しない。
一次ソース: https://github.com/kitao/pyxel/blob/v2.9.5/crates/pyxel-core/src/screencast.rs
これとRSS曲線は初期充填が60秒以降も続く可能性に整合するが、native内部枚数を測った証明ではない。
診断器の4096時間値も低FPSでは充填に時間がかかる。

## schema4の最小補強・確認

- 毎秒samplesへ累積update_count/draw_countを追加。drawは録画capture回数の代理指標で同一保証なし。
- cgroup/schedstat読取りのperf_counter開始/終了を追加し、JSON結果へstarted_perf_counter_sを保存。
  結果基点と対応させ、update後にカウンタ読取りが遅れた窓を判別できるようにした。
- 最大値/閾値/4096件/20分8ケースは変更なし。監視/判定親・systemd設定は変更なし。
- 既存の実子プロセステストに件数増加と読取り時刻順序の確認を追加。診断関連10件成功（3.028秒）。
- tracemalloc2ケースはschema4変更前に起動したschema3。schema4の20分完走はまだ未観測。
- 有効Leaseなし、監視PID1515922/idle子2113951を維持。次の子起動から新診断器を読む。

## 次の検証

1. draw/保持件数とRSSの初期増加が落ち着いた後の窓でメモリを比較する。
2. cgroup/schedstatの読取り時刻を揃えて実行待ちを再評価し、次の2秒イベントを追う。
3. 必要ならpuzzle録画なしの長時間対照、samples保持抑制の調査用対照で残存増を分ける。
   今回のtracemalloc80秒を長時間・nativeリーク検証済みと扱わない。
4. Windows待ちを理由に止めず、原因未特定のまま継続計測。製品修正確定・上流PRなし。
