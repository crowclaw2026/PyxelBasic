# PyxelBasic ソースレビュー（2026-11-11）

対象依頼: [レビューIssue #90](https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/90)。
親: [継続調査Issue #11](https://github.com/SPSoft-Takeshi/crow-loop-v2/issues/11)。
基準: `a870833dcd50c16837b0b6352d721e48ba0d7f7b`。
元リポジトリは `TakeshiMaeda/PyxelBasic`、成果保存先は `crowclaw2026/PyxelBasic` の
`review/loop-source-20261111`。作業場所は `/home/take/projects/spsoft/PyxelBasic-review-20261111`。

## 結論

**元のWindowsでの「PC全体が数秒止まる」原因は特定していない。**
一方、アプリの長いフレーム・資源増大につながる構造と、データ破損・待機残存などの
不具合を確認した。既存テスト成功は、以下の未試験境界が安全であることを意味しない。
製品コード、実稼働worktree、監視サービス・timerは変更していない。上流push・PR提出なし。

優先度は P1=停止・資源圧迫・データ消失を優先的に対処、P2=条件付きの機能障害・観測不備、
P3=低頻度の入力検証不足。確度は「再現」「静的確認」「未確認」を区別する。
P1でもOS全体停止の証明や実利用頻度の高さを意味しない。

## 読んだ範囲と未レビュー範囲

下表の製品Python・診断Pythonは、基準SHAの全行を実際に読んだ。行番号はそのSHAに対応する。
既存の診断メモだけを再要約したものではない。

|ファイル|確認した行|観点・結果|
|---|---:|---|
|`main.py`|1–138|CLI型検証、遅延import、App起動。R9|
|`pyxelbasic/app.py`|1–215|初期化、main/thread分岐、入力・drain・draw・終了。R1/R4/R7/R8|
|`pyxelbasic/session.py`|1–587|I/O仲介、実行・INPUT・BREAK、直接命令、ファイル操作。R1/R6/R8|
|`pyxelbasic/interpreter.py`|1–1646|字句解析、式、IFコンパイル、全命令、資源・配列・RENUM。R1/R2/R3/R5|
|`pyxelbasic/runtime.py`|1–381|全キュー、Condition/Lock、入力、sprites、直接描画。R4/R7|
|`pyxelbasic/audio.py`|1–101|MML生成、4ch、割込み復帰フラグ・停止。後述の未確定点|
|`pyxelbasic/console.py`|1–175|画像・palette・図形・sprite・text描画。主スレッド経路を確認|
|`pyxelbasic/textscreen.py`|1–258|固定グリッド、スクロール、編集再配置、公開snapshot。R1の出力先|
|`pyxelbasic/editor.py`|1–89|カーソル、挿入/削除・画面内reflow。外部無限履歴なし|
|`pyxelbasic/filestore.py`|1–93|拡張子・ファイル全読込・SAVE。R6とI/O制約|
|`pyxelbasic/keywords.py`|1–146|命令/関数対応、frame-break定義・キー対応|
|`pyxelbasic/errors.py`|1–159|例外型・エラー対応表。import時整合assertあり|
|`pyxelbasic/__init__.py`, `version.py`|1–16 / 1–8|公開APIの遅延import、版情報|
|`tools/diagnose_stall.py`|1–232|全診断経路・計測時間/保持・終了。D2|
|`tools/monitor_stall.py`|1–175|子監視、制限、イベント、保持・状態更新。D1/D2|
|`tools/judge_stall.py`|1–67|イベント移管、Gateway結果、再試行。D1|
|`tools/benchmark_stall_timings.py`|1–38|時間系列だけの合成比較。実アプリ再現とは区別|
|`web/app/web_main.py`, `webfilestore.py`|1–38 / 1–50|main固定、JS storage仲介・例外変換|
|`web/build.py`|1–134|成果物組立、版固定、subprocess。今回は実行せず|

補助確認: `tests/test_core.py` のテスト構成・MockIO・主要境界テスト、
`tests/test_stall_{judge,monitor,timings}.py` 全体、`config/systemd/` の3定義を読んだ。
参考資料: `docs/STALL-INVESTIGATION.ja.md`、`docs/CONTINUOUS-STALL-INVESTIGATION.ja.md`、
`diagnostics/loop-11/coverage-20261110/REVIEW.ja.md`。過去計測と今回計測を混同しない。

未レビュー: `web/site/js/*.js`、`web/alt/storage-localstorage.js`、HTML/CSS、全BASICサンプルの
逐行監査、Pyxel/Rust/SDL/音声ドライバの内部実装、ブラウザ・Pyodide実環境、Windows実機。
`samples/alltest.bas` は既存coreテスト経由、`tools/stall_cases/audio_cycle.bas` は実起動経由で
検証したが、全サンプルを体系的にレビューしたという意味ではない。

## 実行・ロックの経路

- main: `App.update:133` → 入力 → `Session.run_frame:370` → `Interpreter.step:831`。
  既定8000エントリ/フレーム。PRINT/描画/一部関数のframe-breakは**命令評価後**に作用する。
- thread: `Session.run:271` → `_run_cycle:420`。既定400命令/64ms。
  描画はgfx queue、音声は独立audio queueへ送り、`App.update:137–139`が主スレッドでdrainする。
  POINT/PLAY/PLAY()は同期要求。VSYNCはthreadでは無効。
- `CommandQueue.drain:227–244`は同じConditionを保持したままバックエンドを呼ぶ。
  Condition.waitは待機中にロックを解放するので、通常のproducer待ち自体はデッドロックではない。
  gfx/audioは順次drainされ、標準surface/audioから他キューへ逆順取得する経路は見つからなかった。
  ただし長いnative処理と例外は別問題（R4）。
- SpriteTableのロックは短いデータ更新/コピーに限定。textはVM所有の固定グリッドをsnapshotとして渡す。
  CPythonでの参照公開を前提とする設計であり、別Python実装・free-threaded環境は未検証。

## 製品の指摘

### R1 — P1: 命令数上限は時間上限ではなく、重い単命令を中断できない

**確度: 構造は静的確認、単一PRINTのフレーム超過は実測、OS停止との関連は未確認。**
`session.py:370–396,420–436` はstepの前だけBREAKを調べる。
`interpreter.py:327–364` の文字列連結/任意精度整数の累乗、`:807–810,1587–1590` のDIM、
`:943–967` のPRINT → `textscreen.py:83–92` の文字単位描画は単命令内で仕事量が増える。
`prepare_run:663` の全プログラム処理もframe予算外。
入力と描画はmainのstepが戻るまで進まない。threadでもGIL保持/キュー主スレッド処理次第で影響し得るが、
その程度は未測定。単にsteps-per-frameを下げても重い1命令は分割されない。

再現: 実TextScreenに262144文字を `run_frame(1)` のPRINT一命令で出力すると **114.28ms**。
ホストのスケジューリングを含む一回値で、CPU占有時間やWindowsの数秒停止とは同一視しない。
Eventでstepを一時停止した制御テストでも、request_break後はstep解放までRUNのまま、次step前にBREAKした。
これはキャンセル位置の確認であって、本物の重いnative命令の強制中断テストではない。

候補: 命令間の壁時計予算に加えて、DIM/文字列/整数サイズ制限、長いPRINT等の分割・協調キャンセル。
互換性とBASIC実行速度の要件を決めてから修正する。次は実際に問題のあったプログラムの命令別時間を採る。

### R2 — P1: 配列・文字列・制御スタックに総量上限がない

**確度: 上限なしと小規模増加は再現。OOMやPC全体停止は起こしていない。**
`dim_array:807` は各次元+1の積/総バイト/次元数を検証せず再帰リストを構築する。
`_ensure_array:812` は未宣言配列に各次元11要素を自動確保するので、添字の数だけで指数的に増える。
`_do_gosub:1018` はRETURNしない再帰でもappendし続け、`_do_for:1090` はGOTOで同じFORへ戻るたび
フレームを追加する。文字列倍増や任意精度整数にも共通の資源予算がない。

安全な検証: GOSUB 2000step→深さ2000、FOR/GOTO 2000step→深さ1000、文字列20回倍増→1048576文字。
DIMは200001要素までに限定。無制限負荷、OOM、サービスへの資源圧迫は行わなかった。
大量確保はプロセス/OSのメモリ圧迫候補だが、元現象時のプログラムが不明なため原因扱いしない。

`reset_runtime:647` は次RUN/NEWで配列・変数・スタック・code/dataを置き換えることを確認し、
再RUNの配列解放・NEWのスタック初期化も検証。一方END/BREAK直後は検査用の実行状態が残り、
メモリが直ちにOSへ返る保証はない（Pythonアロケータの保持と参照リークを区別）。
候補: 配列の積と次元数・文字列/整数・両スタックの上限を確保/append前に検証する。

### R3 — P1: 配列の次元数不足を許し、配列の行をスカラーで上書きする

**確度: BASICプログラムで再現。元の停止との関連なし（証拠なし）。**
`get_array/set_array:818–828` → `_nd_get:1593` / `_nd_set:1602` は
`len(indices)==len(size)` を検証しない。
`DIM M(1,1):M(0)=7:PRINT M(0,0)` で本来listの `M[0]` が7になり、後続アクセスで
`'int' object is not subscriptable`。最後は安全網でEDITへ戻るが、破損自体は防がない。
読み出し側も少ない添字で内部listを返し得る。多すぎる添字/空引数も専用エラーにならない。
候補: 読み書き/暗黙DIMより前に非空・次元一致を検証。寸法の非負・有限値も合わせて検証する。

### R4 — P1: drain中の例外で同期要求が取り残される

**確度: バックエンド例外注入で再現。実Pyxelでの発生頻度・元停止との関連は未確認。**
`runtime.py:193–244` のpget/callはholderの更新を待つが、drainは要求をpopleftした後に
バックエンド例外が起きるとholder設定もnotifyも実行しない。
`App.update:137–139` はこの例外を受け止めず、`App.run:126–130` の後片付けもfinallyではない。

playing()がRuntimeErrorを投げる偽バックエンドで、キューが空になっても呼出しスレッドが残り、
明示的q.stopで解放されることを確認した。実Pyxelは例外でプロセス終了する場合もあり、
常にUIだけ残るデッドロックになるとは断定しない。`audio.py:68–71,84–87` のMML解析例外は
不正MMLに変換済みだが、`:52–56` のplayや他のnative処理は同じ保証がない。

drainはロック保持中にnative呼出しするので、その呼出し自体が止まるとstopもロック取得待ちになる。
候補: 結果/例外の明示的holder、失敗時も通知、Appのfinally cleanup、長い処理をロック外へ出す場合の
「適用済み」順序保証の設計。無造作なロック外移動はwait_emptyの意味を壊す。

### R5 — P1: RENUMのstep=0で複数行が同じキーに上書きされる

**確度: 再現。元の停止とは別のデータ消失。**
`session.py:528–532` → `interpreter.py:597–614`。
`RENUM 10,0` に対する正数step検証がなく、全行が番号10に写像され最後の1行だけになる。
再現ではPRINT 1/PRINT 2の2行が `10 PRINT 2` のみになった。
候補: start/stepと写像の一意性を変更前に検証し、失敗時は元programを保持する。
RENUMの式構文・負数取扱いも別途仕様確認する。

### R6 — P2: LOAD失敗が非トランザクションで、起動時例外処理も弱い

**確度: 偽FileStoreによる不正ファイル読込で再現。元の停止との関連なし。**
`Session._load_file:568–581` はread完了後にNEWして、各行を順次変換・保存する。
後半行の変換失敗で元programは復元されず、部分ロードが残る。
再現: 元の100行を持つ状態で、`10 PRINT 1` と5000桁行番号を含む内容をLOAD。
Python3.12の整数文字列変換上限でエラー表示するが、残るのは新しい10行だけ。

`_submit_line:473–481` の数字先頭処理はtry外、constructorのautoloadも`:262`でtry外。
5000桁の直接入力APIは例外が外へ出る。既定画面の編集長では5000桁は入らないので、
通常UI入力で直ちに起きるとは主張しない。**ファイル経由のautoloadでは画面長制限がない**。
startup autorunのprepare_runもBasicError以外（深い構文の再帰上限等）はconstructor外へ出得る。

候補: 仮programへの全行検証後に交換、起動時/番号付き行でも統一した例外処理。
ファイル内容長・行長も事前制限。通常UTF-8不正はread段階なのでNEW前に失敗する点とは区別する。

### R7 — P2: 満杯の入力リングがキー解放も捨て、押下状態が残る

**確度: 既定256件リングで再現。OS停止原因ではない。**
`App._capture_input:168–185` → `InputRing.push:104–110` → `KeyState.apply:135–144`。
char/repeatとupが同じリング。容量超過をmainは確認しない。
DOWN適用後に256文字を詰めてUPを送るとUPが落ち、drain後もSTICK方向の押下が残った。
VMが重い/待機中の入力や大量貼付で起こり得る。次の押下/解放まで自動再同期がない。
候補: level snapshotで補正、UP優先/別経路、ドロップ統計。キーを無限保持する設計ではなく
「文字の有界化とキー状態整合性は別」という指摘。

### R8 — P2: 終了時のスレッド停止・回収はbest effort

**確度: 静的確認。実障害の再現はしていない。**
`App.run:124–130` はstopを送るだけでjoinせず、session threadはdaemon。
`Session.request_stop:300` のフラグはrun外周でしか見ないため、実行中命令・cycle残り・sleepを中断しない。
R4のlock待ちや重い単命令があれば速やかな回収は保証できない。
診断器はjoin(timeout=1)と残存フラグを持つが製品とは異なる終了手順。
今回の短いaudio-thread試験では残存なし。Pyxel quitがPythonへ戻らずプロセスを終了する経路も
未検証なので「毎回リーク」とはしない。候補: finally、協調停止、有限joinと未終了の明示。

### R9 — P3: thread用CLIの周期にnan/infを受理する

**確度: parserで再現、thread実行時影響は静的説明。元現象との関連なし。**
`main.py:45–49` はv<0だけ判定するので `--vm-cycle-ms nan` / `inf` が通る。
`Session.run:281–284` でnanは比較が成立せず無制御実行、infはsleepでOverflowErrorになり得る。
main modeではこの周期は使わない。候補: math.isfiniteによる拒否とCLI境界テスト。

## 描画・音声・ファイルの点検で確認したこと/限界

- Python製品コードのPyxel画像/入力/音声呼出しはApp/console/audioに集約され、thread VMからは
  SessionIO→queue経由。主スレッド制約を意図的に破る直接呼出しは見つからなかった。
- putは容量制限と停止通知がある。pget/callは容量チェックを迂回するが、現在の単一VM producerは
  同期応答を待つため無制限投入を続ける経路ではない（最大容量+1になり得る）。
  汎用クラスを複数producerに転用すればこの保証は失われる。resetが待機holderを捨てる危険もあるが、
  現行SessionのRUN/BREAKからresetを呼ぶ経路はないため実動作の確定バグには数えない。
- arcは最大720分割。sprite表示表1024件、pattern texture256x256、SETの最終pattern範囲制限あり。
  ただしSETは上限判定前に文字列全走査する。巨大座標・半径/色のnative変換・clippingの計算量は
  Python側だけでは保証できない。画像サイズが固定でも全命令の時間一定は証明できない。
- SoundはPLAYごとに新規作成、Python側はpreparedの一時listと_active4フラグだけで、
  過去Soundを無制限appendするPythonコンテナはない。これはnative側の参照寿命/復帰対象の
  深さやリーク不存在の証明ではない。MML文字数/展開時間の上限がなく、解析はmain上で同期。
  例外捕捉は時間/メモリ上限にならず、MemoryErrorまで不正MMLに見せる可能性がある。
- `_active`は自然終了時には落ちず、resumeの実際の復帰はPyxelに依存。
  既存fakeテストでフラグ列は正しい。今回の3秒dummy音声main/thread試験もエラーなしだが、
  多段割込みからの実BGM復帰、実デバイス・長時間Sound寿命、native lockは未確認。
  次は単独子プロセスでMML長/割込み深さを制限して比較し、nativeの保持参照も確認する。
- 録画はApp.initで設定しておらずPyxel既定依存。既存メモの録画ring観測は今回再計測していない。
  今回のレビューだけで録画が元停止の原因/無関係とはしない。
- LocalFileStore.loadは全量read、SAVEはopen("w")で直接上書きし、サイズ制限・atomic保存なし。
  大ファイル/遅いFS/容量不足では長い待ちや不完全保存があり得る（今回はディスク障害未再現）。
  workdirは起動時固定でもjoinは絶対パス/../を排除しない。従ってworkdirはサンドボックスではない。
  SAVE/LOADは通常直接モード命令であり、これだけで遠隔攻撃や自動実行中の任意書込と断定しない。
- CLIは--runにload必須、--versionはPyxelをimportせず終了できる設計。
  Windowsのsleep粒度補償コメントは過去環境の説明で、今回Windowsで測った事実ではない。
  monitor/judgeはfcntl・/proc・POSIX process group依存のLinux専用運用で、Windows移植未対応を
  製品Windows不具合と混同しない。

## 診断・常駐監視の指摘（製品と別）

### D1 — P2: pending読取→置換とjudgeのrenameに共通ロックがなく二重配送し得る

**確度: 決定的な交差順序注入で再現。本番で既発生かは調べていない。**
`monitor_stall.py:49–58` はpendingをread-modify-write。
`judge_stall.py:20–28` はpending→inflightをrenameするが、両者のflockはmonitor.lock/judge.lockと別物。
旧pendingをmonitorが読んだ直後、judgeが移管し、その後monitorが旧内容+新イベントを書けば、
旧イベントがinflightと新pendingに重複する。再現結果はinflight=[old], pending=[old,new]。
実スケジューリングの確率試験ではなく、該当窓にrenameを差し込んだ論理検証。
余分な判断/重複外部記録・モデル呼出しの原因になり得る。今回モデルは呼ばず一時dirだけで再現。
候補: enqueue/claimに共通の短いロック、イベントIDと処理済み照合。単なるatomic renameでは
read-modify-write全体を排他にできない。

### D2 — P2: ハング/エラー/資源の観測範囲とオーバーヘッドを限定して読む必要がある

**確度: 静的確認、最終画面判定の盲点は合成状態で確認。実際の見逃し件数は未測定。**

|箇所|条件と影響|次の検証・候補|
|---|---|---|
|`diagnose_stall.py:129–140,162–180`|App構築後に監視タイマーを開始、期限はoriginal_updateが戻ってから判定。単独起動は起動中/単命令ハングで期限超過し得る|別プロセスwatchdog。常駐monitorの外側期限は既にある|
|`monitor_stall.py:101–115`|RSS384MiB/heartbeat15秒は1秒間隔、起動猶予30秒。子1プロセスのRSSでありホスト全体・全子孫の圧迫を完全には見ない|systemdのMemoryMaxと合わせて解釈。瞬間ピーク・ログI/Oを別採取|
|`diagnose_stall.py:145–180`|サンプルJSON化、flush、/proc/cgroup読取はupdate計測のended以後。update値に入らず次フレーム間隔・CPU/RSSには入る|サンプリング自身の時間、記録なし対照。frameとupdateの差を製品だけに帰属しない|
|`diagnose_stall.py:139,175,205–216`|3系列は4096件、count/maxは全期間だが分位点は末尾。毎秒samplesは終了まで保持し1時間上限。全割当が一定ではない|保持件数/quantile_scope必須。必要ならsamplesも逐次集約|
|`monitor_stall.py:36–47`|BASICエラー判定は最終screenのみ。過去のエラーが消えれば見逃し、普通の文字列「?ERROR」は誤検出候補|合成screen=[OK]は正常判定を確認しただけ。エラーイベント累積/構造化記録を追加する案|
|`diagnose_stall.py:203,216`|1秒join後もVM生存ならscreen.charsをliveで読む。snapshotではなく未完了VMと競合する可能性|残存を先に明記しpublished snapshotを使用する案|
|`monitor_stall.py:60–66`|正常/異常各24件。異常でも循環、markerを書けず中断したrunはどちらにも属さず削除対象外|重要証拠の別保存、孤児run/総ディスク量の監視。今回は既存証拠を削除せず|
|`judge_stall.py:30–65`|6時間retry、terminal status確認はあるがstatus=okだけではIssue記録の完遂まで保証しない。inflight滞留時にpendingが溜まる|既存Gatewayテストは伝送層。成果物・Issue記録の完了とは別に扱う|

またCPUQuota75%/Nice/MemoryHighは監視対象自身の遅延を作り得る。
CPU/RSSは診断器を含み、dummy/offscreenはWindowsのGPU・DPC/ISR・物理音声の観測ではない。
faulthandlerの2秒反復dumpはスタック採取の補助で、ネイティブ内部のロック所有者やOS停止原因を
証明するものではなく、長時間ハングではログ量も増える。今回サービス構成/閾値は変更していない。

## 今回の検証と保存証拠

証拠: [`diagnostics/source-review-20261111/`](../diagnostics/source-review-20261111/)。
再現は [`reproduce.py`](../diagnostics/source-review-20261111/reproduce.py)、
生の結果は [`reproduction.json`](../diagnostics/source-review-20261111/reproduction.json)。
assertは**現状の不具合/構造を再確認する調査用**であり、修正後の望ましい仕様の回帰テストではない。

- Python3.12.3、Pyxel2.9.5、Linux。独立worktreeにpipなしvenvを作成し、既存venvのPyxelを
  PYTHONPATHで読取参照。既存venv/OSのパッケージは変更していない。
- 追加再現スクリプト: exit0。資源増加、配列破損、同期例外待機、キー解放欠落、キャンセル位置、
  単一PRINT、RENUM消失、LOAD部分反映、番号変換例外、非有限CLI、イベント二重引継ぎを確認。
- `tests/test_core.py`: **300 passed, 0 failed**。
- `python -m unittest discover -s tests -p 'test_stall*.py' -v`: **10件成功**。
  monitorの実Pyxel idle子プロセステストも含む。judgeテストのGatewayはmockでありモデル呼出しなし。
- audio_cycle: main/threadを**直列**に各3秒、SDL offscreen/dummy、外部timeout15秒で実行。
  両方exit0、BASICエラーなし、session_thread_alive=false。
  main update最大4.50ms、thread最大0.37ms。この短い成功だけで長期リーク/復帰/実デバイス障害を否定しない。
- 結果JSON、毎秒JSONL、テスト/診断の標準出力ログを保存。
  `.log`は全体gitignore対象なので証拠だけ個別に追跡する。

再実行（リポジトリ直下、既存依存を変更しない）:

```bash
PYTHONPATH=/home/take/projects/spsoft/PyxelBasic/.venv/lib/python3.12/site-packages .venv/bin/python diagnostics/source-review-20261111/reproduce.py
PYTHONPATH=/home/take/projects/spsoft/PyxelBasic/.venv/lib/python3.12/site-packages .venv/bin/python tests/test_core.py
PYTHONPATH=/home/take/projects/spsoft/PyxelBasic/.venv/lib/python3.12/site-packages .venv/bin/python -m unittest discover -s tests -p 'test_stall*.py' -v
```

他環境ではその環境の既存Pyxelを使う。診断のoutputは既存結果を上書きしない新名にする。

## 残件・推奨順序

1. データ消失R3/R5/R6、同期例外R4の修正仕様と回帰テストを別タスクで用意する。
   今回はレビューであり、推測した製品修正を混ぜない。
2. R1/R2の資源上限・時間予算は互換性に関わるため、実際のプログラム負荷を採って設計する。
3. D1/D2の監視改善を親Issueへ引継ぐ。サービスは停止・再起動せず、独立検証後に別途反映する。
4. 音声・録画・native寿命、Windowsの実症状は未確定。発生時プログラム/版/引数/時刻と
   OS側計測が得られたら照合するが、情報待ちでLinux継続監視を止めない。

**このソースレビューTaskの完了と、親Issue #11の原因調査完了は別。親の継続計測は継続する。**
