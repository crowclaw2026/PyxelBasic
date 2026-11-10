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

## 検証

`python -m unittest discover -s tests -p test_stall_monitor.py -v`:
既知の録画バッファ増をリーク扱いしない、異常検出、6時間重複抑止、保持上限、
本物のPyxel子プロセスでの計測完了を検証する。音声cycle main/threadの実起動も確認する。
