# 2026-11-10 初回coverageレビュー

対象: Issue SPSoft-Takeshi/crow-loop-v2#11。製品修正ではなくfork上の診断改善。
Linux offscreen / dummy音声 / systemd CPUQuota75%、Pyxel2.9.5 / Python3.12.3。
初回8ケース各1200秒、全てexit0。2秒フレーム、定常RSS64MiB増などの監視閾値超過なし。
元のWindowsでのPC全体数秒停滞は再現・原因特定していない。

|case|平均CPU(論理1CPU比)%|update最大ms|frame最大ms|60秒→終了RSS増MiB|
|---|---:|---:|---:|---:|
|idle|11.68|5.86|191.01|9.50|
|particle-main|74.26|529.71|546.53|10.10|
|particle-thread|51.95|5.32|104.58|9.68|
|audio-cycle-main|12.92|8.24|146.68|10.18|
|audio-cycle-thread|21.60|4.59|69.07|10.15|
|puzzle-main|73.48|1175.13|1179.60|14.78|
|cube-main|66.05|247.97|303.91|9.17|
|idle-no-capture|12.15|1.99|98.01|9.95|

## 絞れたこと・未確定事項

- mainのVMは `pyxelbasic/app.py:165` → `session.py:370 run_frame` で命令数上限まで実行し、壁時計の時間予算はない。
  particle/puzzleは平均CPUが75%上限付近。mainの重いフレームは以前の短時間試験とも整合。
  サービスのcpu.statで累積nr_throttled=20866 / throttled_usec=359478069を観測したが、
  初回ログにはケース単位のcgroupカウンタがない。各最大遅延の原因をthrottlingと断定しない。
- 1秒毎のキュー概数はgfx最大0、audio最大2、GOSUB最大1/FOR最大2。
  持続的なバックログ/制御スタック増大はこの範囲で観測なし。サンプル間の瞬間増大は除外できない。
- 録画なしidleでも60秒以降約9.95MiB増えた。録画リングだけではこの増分を説明できない。
  旧診断器はフレームごと3本のPython floatリストを保持するため、RSSには計測器分を含む。
- `tools/benchmark_stall_timings.py` は別プロセス2本でフレーム時間保持だけを比較する。
  3600→72000フレーム相当で全保持は8,335,360 bytes増、4096件制限は61,440 bytes増。
  この合成試験はApp/Pyxelを動かしておらず、旧リストと全保持dequeの割当差もある。
  計測器だけで数MiB増え得る証拠であり、実Appの増加全量の説明やリーク否定ではない。
  puzzleの増分14.78MiBはフレーム数が少ないため、計測器の3系列だけで説明せず追加検証する。

## 診断変更（schema_version=2）

- stdlib dequeで各時間系列を既定4096件に制限。新依存なし。
- countとmaxは全期間を保持し、2秒異常検出を失わない。分位点は末尾保持分のみとなり、
  `retained_count` / `quantile_scope` を明記。旧結果の全期間分位点とそのまま比較しない。
- `--timing-capacity 0` で比較用の全件保持が可能（メモリが増えるため常駐既定にはしない）。
- 毎秒サンプルに自身のcgroup-v2 `cpu.max` / `cpu.stat` を追加。カウンタ差分で制限による待ちを追う。
  cgroup不在/非Linuxはnull。親cgroup/ホスト競合までは除外できない。
- 毎秒のsamplesリストは残る（最大3600秒）。診断器の全割当が一定になるという変更ではない。

## 検証と安全な反映

- `python -m unittest discover -s tests -p 'test_stall*.py' -v`: 10件成功（3.023秒）。
  evict済みの3秒スパイクも全期間maxに残り、末尾分位点と区別されることを確認。
  実Pyxel子プロセス・イベント重複抑止・判定失敗保持・空キューLLM非呼出しも成功。
- 別の一時systemd unitでCPUQuota75%、実Appを3秒実行。exit0、181 update、保持8件、
  cgroup cpu.max=`75000 100000` とnr_throttled取得を確認。`live-check.json`参照。
  最初の試験コマンドは誤ったApp引数でexit2、Appは起動せず、修正したコマンドで上記成功。
- 既存LOOP runtime/leasesにはIssue11のSHA256キー/旧番号キーのLeaseなし。残る隠し.lockはLeaseではない。
- 監視サービスの再起動、子プロセスへのシグナル、systemd設定変更なし。
  既存の監視親は毎回診断スクリプトを別プロセスで読むため、次回子起動から新計測器を使用する。
  実行中idleは旧方式のまま完走させる。新方式の20分完走はまだ未観測。

## 次の検証

1. 次周のschema2・4096件計測でidle/録画なしidleの60秒以降RSS傾向を旧結果と比較。
2. particle/puzzleの長いフレーム付近でcgroup nr_throttled/throttled_usecの差分とCPU/PSIを照合。
   制限影響の疑いと元現象を分け、必要なら同一ケースを別quotaで直列比較する。
3. puzzleの残存RSS増加は録画なし/thread条件や診断器のsamples割当・アロケータ保持と比較。
   このレビューでは既存8ケースを維持し、追加ケースの常駐投入はまだしない。
4. Windows証拠がなくてもLinuxの継続計測は止めない。上流PR/製品反映なし。

## 保存証拠

`summary.json` が実測表の元データ。各runディレクトリには初回結果・ホストJSONL・meta・logをgzip保存。
`sha256.json` は展開前の元ファイルのSHA256（圧縮データのhashではない）。
元の常駐runsが保持上限で循環しても、この初回比較証拠はforkへ保存する。
`timing-allocation.json` は合成割当試験の保存結果。
