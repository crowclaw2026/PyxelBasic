# 2026-11-17 ホスト再起動による試験中断

対象: Issue #11、fork source HEAD `9be66ea491fa19078c342ffd19342e63fe19a3c3`。
Linux offscreen / dummy音声 / CPUQuota75%。Windowsの元現象と同一視しない。

## 分類と証拠

- 16:45:58 JSTにlogindがホスト再起動を記録。16:46:00にmonitorサービスは正常停止、16:46:06にsystemd-reboot完了。次bootで16:46:25に監視が起動した。既存作業記録の手動再起動とも整合。
- 中断対象は `run-20261117T072957.733776Z-particle-thread`。1200秒予定に対し最後のsampleは962.734秒、16:46:00.675 JST。結果JSONは0bytes、slow JSONLは0bytes、process.logはdeprecation warningのみ。完了マーカーなし。
- 最後のsampleの累積最大値はupdate79.105ms、draw138.540ms、frame477.014ms。RSS138,067,968bytes。終了直前まで進捗があり、SIGSEGV等を示す記録はない。ただし失われた終了区間について正常完走を主張しない。
- `tools/monitor_stall.py` mainは起動時の残存active_runからこのイベントを生成する。イベント名だけではアプリ/監視クラッシュとは判定できない。next_caseを試験前に進める設計により次のaudio-cycle-mainへ復帰。中断試験はcompletedに加算されず522のまま。
- 新親PID1111、子1156。healthのheartbeat更新と経過467秒以上、monitor active/running・judge.timer activeを確認。常駐サービスをこの調査で停止/再起動していない。

## 環境境界と次の検証

直前の完了runはkernel7.0.0-38-generic、再起動後は7.0.0-43-generic。Python3.12.3/Pyxel2.9.5は同じ。今後はこのboot/kernel境界を分けてRSS/遅延を評価し、CPUQuotaや観測器の影響と区別する。

今回のホスト再起動は試験中断を説明するため、アプリ障害再現試験・製品修正・監視設定変更は不要。継続監視で次の完了/異常を観測する。Windows元現象および既報puzzle SIGSEGVの原因を今回確定/否定したものではない。

有効LOOP Leaseなし、調査開始時作業ツリーclean。調査報告と証拠のみforkへ保存。元run・稼働子・監視状態ファイルには変更しない。manifestは非圧縮原本のSHA256、sampleはgzipで保存。host.jsonlは元stateディレクトリに残し大量複製しない。
