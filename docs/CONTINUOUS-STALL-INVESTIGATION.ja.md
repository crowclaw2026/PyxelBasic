# Issue 11 継続調査の運用（2026-11-10）

Takeの指示で、短時間試験後にBlockedとして待つ方式を廃止した。
`monitor_stall.py` は既存の `diagnose_stall.py` を直列に起動する。
独自のスケジューラ/常駐LLMではなく、systemdの再起動・timerと標準ライブラリを利用する。
製品コードへの変更なし。計測用起動器の試験時間上限を3600秒へ延長し、逐次サンプルへ
最大フレーム/update/draw時間を追加した。実運用は各1200秒。

## 稼働構成

- `crow-pyxelbasic-monitor.service`: 常駐。各20分の8ケースを直列反復（約2時間40分で1周）。
- `crow-pyxelbasic-judge.timer`: 1分おきにローカルイベントファイルの有無を確認するだけ。
- `crow-pyxelbasic-judge.service`: キューがある場合のみGateway経由でLLM判断。
- 監視と判断は別サービス。LLMの待機・失敗で計測を止めない。
- unit定義は `config/systemd/`。導入先は `~/.config/systemd/user/`。
- 状態は `~/.local/state/crow-pyxelbasic/`。他の測定・旧成果には触れない。
- Projects: Running / Executable=No。Runningはsystemd調査でありLOOP WorkerのLeaseではない。
  Dispatcherの同一Issue起動と重複しないためにExecutableをNoとした。

## 計測と検出

- Linux・SDL offscreen・dummy音声。idle、particle main/thread、
  audio_cycle main/thread（BGM割込み・復帰・停止・再開）、puzzle、cube、録画なしidle。
- 2秒以上のupdate/draw/frame間隔、60秒後から64MiB以上のRSS増加（60サンプル以上）、
  VMスレッド残存、BASICエラー、非0終了、不完全なJSON、早期終了をイベント化。
- 子RSS384MiB超、15秒サンプル無更新（起動猶予30秒）、試験期限+45秒超は
  該当プロセスグループだけ終了し証拠保存。監視本体は次ケースへ進む。
- 結果JSON、1秒ごとのサンプルJSONL、スタックを含むログ、ホストのload/meminfo/PSI/CPU統計を保存。
- Nice=10、CPUWeight=10、CPUQuota=75%（論理CPU1個比）、MemoryHigh512M/Max640M。
  この制限・計測のオーバーヘッドも遅延原因になり得るので、検出を元の不具合と断定しない。
- 正常run24件・異常run24件を別々に保持。初期調査の成果は削除対象外。
  異常が多数続く場合に古い証拠も上限で循環するため、重要な証拠は判断時に別途保存する。

## LLMの起動条件・リトライ

- 同じケース/異常種別は6時間重複抑制。抑制中もrunごとの測定結果は保持。
- 全8ケースの蓄積結果を最大24時間ごとにレビューし、新仮説やケース追加を判断。
  正常1ケースごとや毎分の空チェックではLLMを呼ばない。
- pending.jsonをinflight.jsonへ引き継ぎ、Gatewayが成功した場合のみlast-reviewed.jsonへ移す。
- 判断失敗時は証拠を残し6時間後から再試行。呼出しは600秒、CLIは660秒で期限。
- `judge-status.json` のattempts/last_success/last_okで実際の呼出しを確認できる。
- 明示的な経路確認だけ `judge_stall.py --state-dir ... --probe` を使う。
  probeは異常と偽らず、読取りだけ。通常の運用では使わない。
- LLMは証拠を調べてIssueへ成果・次の検証を記録する。原因不明で監視全体を止めない。
  別Worker稼働時は変更競合を避け、調査案を残す。上流PRや製品反映とは別の調査権限。

## 確認・運用コマンド

```sh
systemctl --user status crow-pyxelbasic-monitor.service crow-pyxelbasic-judge.timer
cat ~/.local/state/crow-pyxelbasic/health.json
cat ~/.local/state/crow-pyxelbasic/state.json
cat ~/.local/state/crow-pyxelbasic/judge-status.json
journalctl --user -u crow-pyxelbasic-monitor.service -n 20 --no-pager
```

監視停止はTakeの明示指示時だけ。コード更新は実行中プロセスと分離して試験後に反映。
これはLinux側の長時間調査であり、Windows実機で元現象を再現したという意味ではない。
Windows側の証拠が後から得られたら、計測を続けたまま同条件を追加する。

## 初回coverageレビュー後の計測（2026-11-10）

初回8ケース各20分完走。結果と限界は
`diagnostics/loop-11/coverage-20261110/REVIEW.ja.md` と保存証拠を参照。
計測器自身のフレーム時間保持がRSSに含まれるため、診断JSON schema2では
各系列を4096件に制限する。全期間count/maxは維持するが、分位点は末尾保持分のみ。
`retained_count` / `quantile_scope` を必ず確認し、旧方式の全期間分位点と混同しない。
比較専用に `--timing-capacity 0` で全保持も可能。毎秒サンプルにcgroup-v2の
`cpu.max` / `cpu.stat` を追加し、制限による待ちを差分で調べる。
監視親と稼働中の子は再起動せず、新しい子起動から診断変更が適用される。

## 2秒超イベント後の計測（2026-11-11）

`diagnostics/loop-11/stall-20261111/REPORT.ja.md`にparticle-mainの実測を保存。
ピーク窓はwall約2.265秒にprocess CPU約0.155秒、当該cgroupのthrottling増分0。
ホスト競合/待機の切分け用にschema3では500ms以上のupdateを`.slow.jsonl`へ保存する。
wall/thread CPU/process CPU、終了時cgroup/schedstatを含む。結果JSON内は末尾64件。
`non_cpu_wall_ms`は原因の断定ではない。定期サンプルのschedstat差分も窓幅に注意する。
監視サービス・設定・ケースは維持し、次回子起動から新診断器を使用する。

## warm-upと読取り時刻（2026-11-12）

`diagnostics/loop-11/coverage-20261112/REPORT.ja.md`に保持中24件とtracemalloc追加試験を保存。
低FPSでは録画リング/4096件時間系列の充填が固定60秒を超え得るため、
60秒以降を一律に定常状態と解釈しない。schema4で毎秒update/draw累積件数を追加。
cgroup/schedstatはupdate後に順次読み取るため、update終了と境界が一致しない。
schema4は読取り開始/終了perf時刻と結果基点を保存し、観測遅延を区別する。
64MiB判定・ケース・監視親は維持。新しい子から反映される。

## 毎秒sample保持の分離（2026-11-13）

`diagnostics/loop-11/coverage-20261113/REPORT.ja.md`に充填後の比較とmemory/stream対照を保存。
schema5は既定で毎秒sampleをJSONLへだけ逐次flushし、計測中の全件list保持を避ける。
停止・最終RSS採取後に結果JSON用の全samplesへ読み戻すため、監視親の既存判定は維持。
最終化時のRSS・所要時間・CPU時間は別metadataで記録し、計測中RSSと混同しない。
比較用`--sample-storage memory`を残す。JSONLへ最終sampleも保存する。
結果samplesとJSONL全行の一致・カウンタ件数等を実子テストで確認する。
この最終化にも有限のメモリと時間がかかり、診断器全体が無負荷になるという変更ではない。
製品・監視service・timer・常駐8ケースは変更しない。次の子から適用。

## 検証

`python -m unittest discover -s tests -p test_stall_monitor.py -v`:
既知の録画バッファ増をリーク扱いしない、異常検出、6時間重複抑止、保持上限、
本物のPyxel子プロセスでの計測完了を検証する。音声cycle main/threadの実起動も確認する。
