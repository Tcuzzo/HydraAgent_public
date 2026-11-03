# Live truth checklist (phase 2)

Read-only probes the coordinator runs before any lane is thrown. Record each one as
a row: the command, the trimmed output, the time. Every later claim points at a row.

Placeholders: `<pid>`, `<port>`, `<repo>`, `<url>`, `<scratch>` (a temp dir you own),
`<pkg>` (the product's import name), `<venv-python>` (the interpreter the process runs).

## 1. Processes, command lines, parent chains

What actually runs, started by what.

- Windows PowerShell
  ```powershell
  Get-CimInstance Win32_Process |
    Select-Object ProcessId, ParentProcessId, Name, CreationDate, CommandLine |
    Where-Object { $_.CommandLine -match '<pkg>|<product name>' } | Format-List
  # walk one parent chain
  $p = <pid>; while ($p) { $x = Get-CimInstance Win32_Process -Filter "ProcessId=$p"; if (-not $x) { break }; "$($x.ProcessId) $($x.Name) $($x.CommandLine)"; $p = $x.ParentProcessId }
  ```
- POSIX
  ```sh
  ps -eo pid,ppid,user,lstart,args --forest | grep -i '<pkg>'      # Linux
  ps -axo pid,ppid,user,lstart,command | grep -i '<pkg>'           # macOS
  pstree -ap <pid>                                                 # parent/child tree
  readlink /proc/<pid>/cwd ; readlink /proc/<pid>/exe              # Linux: cwd and binary
  ```

Watch for: two copies of the same product, a hidden background process behind the
window the human uses, an interpreter from an unexpected venv.

## 2. Listening ports and their owners

- Windows: `Get-NetTCPConnection -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess | Sort-Object LocalPort`
- POSIX: `ss -ltnp` (Linux) or `lsof -nP -iTCP -sTCP:LISTEN` (macOS)

Note the bind address. `0.0.0.0` or `::` means reachable from the network; check
what auth sits in front of it.

## 3. Scheduled tasks, services, timers

- Windows
  ```powershell
  Get-ScheduledTask | Where-Object State -ne 'Disabled' | ForEach-Object {
    [pscustomobject]@{ Task = $_.TaskName; Exec = $_.Actions.Execute; Args = $_.Actions.Arguments; Dir = $_.Actions.WorkingDirectory } }
  Get-CimInstance Win32_Service | Where-Object State -eq 'Running' | Select-Object Name, PathName, StartName
  Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
  ```
- POSIX
  ```sh
  systemctl list-units --type=service --state=running ; systemctl list-timers --all
  systemctl show -p ExecStart,WorkingDirectory,EnvironmentFiles <unit>
  crontab -l ; ls /etc/cron.d ; launchctl list | grep -i '<pkg>'   # macOS
  docker ps --no-trunc --format '{{.Names}} {{.Image}} {{.Command}}'
  ```

## 4. Shortcuts and launchers: what the human's click runs

- Windows
  ```powershell
  $sh = New-Object -ComObject WScript.Shell
  Get-ChildItem "$env:USERPROFILE\Desktop", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs" -Recurse -Filter *.lnk |
    ForEach-Object { $l = $sh.CreateShortcut($_.FullName); [pscustomobject]@{ Name = $_.Name; Target = $l.TargetPath; Args = $l.Arguments; Dir = $l.WorkingDirectory; Icon = $l.IconLocation } }
  ```
- POSIX: `grep -H '^Exec=\|^Icon=' ~/.local/share/applications/*.desktop ~/Desktop/*.desktop`;
  macOS: `defaults read "/Applications/<App>.app/Contents/Info" CFBundleExecutable`

The window the human calls "the app" is the ground truth for what "the product" means.

## 5. Which source tree each process imports

Ask the interpreter the process runs, never assume from the folder name.

```sh
<venv-python> -c "import <pkg>, sys; print(<pkg>.__file__); print(sys.prefix)"
<venv-python> -m pip show -f <pkg> | head -20        # editable install? Location?
node -p "require.resolve('<pkg>')"                    # Node
```
- .NET or other binaries: `(Get-Item <exe>).VersionInfo | Format-List ProductVersion, FileVersion`
  (a `+<sha>` suffix names the commit it was built from); POSIX `strings <bin> | grep -m3 -i commit`.

## 6. Git state of every checkout

Find every checkout first; there is often more than one.

- Windows: `Get-ChildItem <root> -Directory -Recurse -Depth 4 -Force -Filter .git | ForEach-Object Parent`
- POSIX: `find <root> -maxdepth 5 -name .git -prune -print`

Then for each:
```sh
git -C <repo> remote -v
git -C <repo> branch -vv --all | head -30
git -C <repo> status --porcelain=v1 | wc -l          # dirty count; also list the paths
git -C <repo> log -3 --format='%H %P %an %ad %s'      # %P empty = parentless commit
git -C <repo> worktree list ; git -C <repo> stash list
```
If git refuses with a "dubious ownership" error, add `-c safe.directory=*` to that
read-only command and record the ownership problem as a finding.

## 7. What the remote really holds

A local `origin/main` can be weeks stale. Ask the remote.

```sh
git ls-remote <url>                                   # every ref and its sha, live
git clone --no-checkout --filter=blob:none <url> <scratch>/remote && git -C <scratch>/remote log -5 --format='%H %P %an %ad %s'
```
Look for: a parentless tip (history replaced), a tip unknown to every local checkout,
two local repos that both point at the same remote.

## 8. Content-hash comparison of trees

Commit ids and folder names lie about content; hashes do not.

```sh
git -C <repo> worktree add --detach <scratch>/ref <ref>     # or: git -C <repo> archive <ref> | tar -x -C <scratch>/ref
python scripts/cmp_trees.py <running-tree>/src <scratch>/ref/src --labels running remote
```
`cmp_trees.py` normalizes CRLF and skips caches, and prints its skip list. Report the
counts line verbatim: files on each side, only-A, only-B, changed. Remove the scratch
worktree afterwards (`git worktree remove <scratch>/ref`) and say so.

## 9. History the repo forgot: agent session logs

When the why is missing (a force push, a deleted repo, a reset), coding-agent
transcripts on the machine often recorded it. Read only; cite path and line number.

```sh
grep -n -E 'forced update|push --force|\+[0-9a-f]{7,}\.\.\.|Deleted repository|reset --hard|rm -rf' \
  ~/.codex/sessions/*/*/*/*.jsonl ~/.claude/projects/*/*.jsonl 2>/dev/null | head -50
```
On Windows use `Select-String -Path "$env:USERPROFILE\.codex\sessions\*\*\*\*.jsonl" -Pattern '...'`.
Transcripts can carry personal data: quote only the lines you need.

## 10. Brand and identity assets by hash

```powershell
Get-FileHash -Algorithm SHA256 <shortcut icon>, <repo icon>, <installer icon>
```
```sh
sha256sum <icon files>
```
Two icons that look alike are different assets until the hashes match.

## 11. Logs: the right file

```powershell
Get-ChildItem <log dir> | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name, Length, LastWriteTime
```
The newest file in a log dir is often a lock, a rotated archive, a crash dump, or
another tool's output. Confirm by name pattern and by content (timestamps, logger
format) before you count anything in it. Count with the denominator:
`Select-String -Path <log> -Pattern 'ERROR' | Measure-Object` vs total lines.

## 12. Config and environment actually loaded

- The config file the process names on its command line, or its documented default
  path. Read it; never print secret values (print key names only).
- Linux env of a running process, names only: `tr '\0' '\n' < /proc/<pid>/environ | cut -d= -f1`
- Which env file a unit loads: `systemctl show -p EnvironmentFiles <unit>`.

## 13. Data stores

```sh
cp <db> <db>-wal <db>-shm <scratch>/ 2>/dev/null
sqlite3 "file:<scratch>/<db>?mode=ro" '.tables' 'select count(*), max(ts) from <table>;'
```
Freshness (`max(ts)`) and row counts with the time you read them. Two stores holding
"the same" thing is a finding.

## 14. Remote hosts

Same probes over `ssh <host> '<read-only command>'`. Also: the deployed tree vs the
repo tree (`cmp_trees.py` on a copy), and the deployed commit marker if one exists.

## Done when

You can fill this table with a proving row for every cell:

| product piece | process / unit | port | started by | source tree | commit + dirty | remote tip | content match |
|---|---|---|---|---|---|---|---|
