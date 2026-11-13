# 2026-11-13 coverageレビュー（completed=224）

Issue: https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/11
基準SHA: 587ed133dff7dd5789835ebc9b58bca27af5e59a
Linux offscreen/dummy音声・CPUQuota75%・Nice10。Windows元現象とは区別する。

## 確認範囲と新しい計測結果

保持中正常24run（8ケース×3周、各1200秒）が全てschema4/exit0。
新2秒閾値超過なし。全累積224runの精査ではなく保持範囲の調査。
最新8runをgzip/hash付き保存し、全24件の指標はretained-normal-summary.json。

メモリ比較開始は、経過60秒以上・draw600件以上・update/draw/frameの時間保持が全て4096件、
の条件を初めて満たしたsampleとする。録画内部枚数を直接測ったわけではなく、初期充填終了の代理条件。
その後60秒窓のRSS中央値と1080〜1140秒窓の中央値を比較し、間の毎秒sample件数差で割る。
時間系列・描画が進んだ後も、どのケースも1サンプルあたり数KiBの増加が残る。
厳密なリーク率ではなく、割当器ページ粒度・負荷・native等を含む指標。

|case（最新）|update最大ms|frame最大ms|slow件数|代理条件成立秒|RSS増bytes/追加sample|
|---|---:|---:|---:|---:|---:|
|audio-cycle-main|4.953|34.977|0|68.7|3092.9|
|audio-cycle-thread|96.022|246.815|0|68.6|3117.9|
|cube-main|29.843|48.128|0|75.7|3109.5|
|idle|4.790|69.389|0|68.6|3097.0|
|idle-no-capture|4.552|221.627|0|68.6|3084.7|
|particle-main|1068.521|1074.040|14|477.8|3094.8|
|particle-thread|21.953|387.881|0|76.9|3093.1|
|puzzle-main|1231.700|1289.657|12|638.7|3072.0|

この近い増加量は毎秒sample保持の寄与という前回tracemalloc所見と整合するが、単独では全量説明ではない。

## 遅延と読取り時刻

particle/puzzleの500ms超update計37件を再照合。
最大のpuzzle update1231.7msやparticle約1068.5msは500ms超だが2秒未満。
CPU未実行時間の大きいupdateが残る。slow-windows.jsonに各updateと直前sampleの
schedstat読取り開始/終了から求めたカウンタ窓の最小/最大、update終了後の読取り遅延、
runqueue/runtime/cgroup差分を保存。
これらの窓はupdate以外の処理を含むため、単一updateの厳密な待ち内訳にしない。
前回の2秒現象が再現しないことをWindows問題否定には使わない。

最新puzzle最大updateの例では、前定期sample→slow終了の名目窓1254.003msに対し、
schedstatの実読取り開始/終了から求めたカウンタ窓は1291.366〜1291.557ms。
update終了後のschedstat読取り終了まで38.752msかかり、cgroup読取りだけで約38.6msを含む。
そのカウンタ窓でruntime170.787ms/runqueue待ち1109.402ms、当該cgroup throttle増分0。
読取りが遅れる窓の存在を実測でき、実行待ちの所見を更新して維持する。
ただし、counter窓にはupdate前後の処理も含み、個別updateだけの時間配分にはしない。

## 観測負荷を下げるschema5（診断限定）

標準JSONLの既存逐次保存を利用し、新しいサービス/依存ライブラリを作らない。

- 既定`--sample-storage stream`で、計測中は毎秒sample dictを全件保持しない。
  逐次JSONLのflush・異常検出に必要なピーク・全期間countは従来通り。
- App停止/最終RSS sampleの採取後だけJSONLを読み、結果JSONの全samples配列へまとめる。
  監視親が従来通り全samplesでRSS等を判定できる。JSONLには最終sampleも追記する。
- 最終化のメモリ増/所要時間は別metadataに残す。計測中のRSSがゼロオーバーヘッドになるわけでも、
  プロセス全期間の最大RSSが低くなる保証でもない。20分/最大3600秒分の読込みは終了時に発生する。
- 比較専用`--sample-storage memory`で旧方式同等の計測中保持を残す。
- 製品コード・監視親・閾値・常駐ケース・systemd設定は変更しない。
- 診断関連10テストで実子プロセスも確認。streamの結果samplesとJSONL（最終sample含む）が完全一致。
  件数・全期間max・カウンタ読取り順序、既存イベント/保持動作も従来テストで確認。
## memory/stream比較の実測

既存のtracemalloc fixtureを用い、idle各80秒をmemory→streamの固定順で直列実行。
別の一時systemd unit、CPUQuota75%/Nice10/MemoryMax256M、録画0秒、時間系列容量64件。
64件へ揃えたのは20秒baselineまでに時間系列を充填し、sample保持の影響を分けるため。
通常の4096件・録画あり20分runとは異なる診断条件で、性能ベンチマークではない。
tracing/snapshot自身の割当・停止と順序/ホスト負荷の交絡も含む。

|mode|snapshot区間秒|Python追跡正味増bytes|計測中保持sample|結果/JSONL件数|終了時読戻し秒|
|---|---:|---:|---:|---:|---:|
|memory|55.000|157562|80|81|0.000003|
|stream|55.001|8998|0|81|0.033299|

両方exit0・80秒以上・BASICエラーなし・VM thread残存なし・cpu.max=75000 100000。
両方の結果samplesがJSONL全81件と完全一致。streamでもピークや毎秒証拠を落としていない。
この単一ペアでは計測中の追跡増が大幅に小さくなり、sample list保持が観測ノイズに寄与する根拠を得た。
Python割当だけの検証で、native/RSS全量・長時間安定・Windows元現象の解決を示すものではない。
追跡試験中、終了時だけ追加したCPU最終化metadataがある/ない版の違いがあるが、計測中sample保持と
snapshot採取の処理は共通。主要比較は終了前のsnapshotであり、CPU性能比較はしない。

## 次の検証・稼働状態

- schema5で8ケースの20分結果を蓄積し、同じ充填後窓のRSS/追加sampleを再比較。
- 新2秒イベントで時刻境界を揃えたCPU/実行待ち/制限を確認。原因不明・Windows待ちで監視停止しない。
- 新方式の20分完走はまだ未確認。原サービス親1515922と実行中idle子2191083を維持。
- 有効Leaseなし。常駐service/timerはactiveで、再起動・設定変更・子へのsignalなし。
  親は各ケースで診断スクリプトを起動し直すため、次の子からschema5を使用する。
- 製品コード・上流push/PR・常駐ケースの変更なし。

## 保存証拠

最新8runはresult/JSONL/host/meta/log等、残り16runはresult/normal/metaを保存。
全24runの元ファイルをgzip展開・SHA256照合。hashは元データに対するもの。
comparison.jsonと各memory/stream JSON・JSONL・allocations JSON・起動引数・gzip logが比較証拠。
診断関連10テスト成功（3.025秒、tests.txt）。stream全samplesの復元/一致と読取り時刻/件数を実プロセスで確認。
