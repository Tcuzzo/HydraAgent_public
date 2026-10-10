# Platform notes

## Running on Windows and macOS

Hydra works on Linux, macOS and Windows. Three things behave differently depending on
which one you use, because the operating system either gives Hydra a capability or it
does not. Where a capability is missing, Hydra switches that feature off and tells you
out loud. It never pretends a feature is working.

### Running commands: works everywhere

When Hydra runs a command for you, it goes through one translator (`hydra/proc.py`) that
knows how each operating system starts a program. On Linux and macOS it uses the normal
Unix shell. On Windows it uses `cmd.exe`, or Git-Bash if you have that installed. There
is nothing for you to configure.

There is one Windows trap Hydra steps around for you. Windows ships a program called
`bash`, but it is really a doorway into the Windows Subsystem for Linux: a separate world
with its own filing system, where your `C:` folders are not where they normally are. A
command sent through that doorway would run in the wrong place, or simply fail if you
never installed a Linux to put behind it. Hydra ignores that doorway and looks for a real
Git-Bash instead. If a job needs a Unix shell and you have no Git-Bash, Hydra says so
plainly rather than running your command somewhere you did not mean.

### Vector memory: needs the right Python

**The short version.** Hydra remembers what you tell it, and it can look those memories
up two ways. Keyword search finds notes containing the words you typed. Vector search
finds notes that are *about* the same thing even when the words are different: ask about
"the login bug" and it can surface a note that said "auth kept rejecting me". Vector
search is the part that is sometimes unavailable.

**The detailed version.** Vector search comes from a small add on called `vec0` that
plugs into SQLite, the little database Hydra keeps memories in. Two separate things have
to line up:

1. **The add on has to exist for your machine.** The copy bundled here is built for
   Linux on Intel and AMD chips only. On macOS or Windows, install it yourself with
   `pip install sqlite-vec`.
2. **Your Python has to be allowed to load add ons at all.** This is the one that
   catches people out. Python has a switch, set when Python itself was built, for
   whether SQLite may load add ons. Several macOS Pythons ship with that switch off,
   including the one this project's own tests run on. When it is off, no add on can ever
   load, whatever else you install.

Check the second one:

```bash
python -c "import sqlite3; print(hasattr(sqlite3.Connection, 'enable_load_extension'))"
```

`True` means you are fine. `False` means vector memory cannot run on that Python at all.
Either use a different Python (the builds from python.org and Homebrew normally work),
or simply carry on without it. Hydra prints this at startup:

```
Hydra memory: vector lane OFF - <the exact reason>
```

and falls back to keyword search. You lose "find notes about this idea" and you keep
"find notes containing this word". Nothing breaks, and nothing goes quiet.

### Pasting several lines at once: Linux and macOS only

**The short version.** Paste a block of text into Hydra's chat on Linux or macOS and it
arrives as one message. On Windows the same paste arrives as one message per line. Every
line still gets through -- nothing is dropped or reordered -- but a pasted paragraph
becomes several turns instead of one, and Hydra tells you so the first time it happens.

**The detailed version.** When you paste, the terminal drops all of the lines into Hydra's
input at once. Hydra reads the first line and then asks the operating system "is there
more already waiting?", so it can gather the rest into the same message. That question is
a mechanism called `select`, and on Windows it can only be asked about network
connections, never about a console window. So on Windows Hydra cannot find out that more
lines are waiting, and it takes the first one. The rest are not lost: the console holds
them and hands them to Hydra as the next messages.

If you want a pasted block to land as a single turn on Windows, save the text to a file
and point Hydra at the file. The test that proves the gathering works needs a
pseudo-terminal -- a fake keyboard-and-screen that Windows does not have -- so that one
test skips there, and only there.

### Noticing a busy graphics card: Linux and macOS only

**The short version.** If a graphics card in your machine is already busy with another
job, Hydra should send new work to a cloud model rather than pile onto the card and make
both jobs slow. On Windows it cannot tell whether the card is busy, so it assumes the
card is free.

**The detailed version.** The job using the card leaves a marker file and holds a *lock*
on it. A lock is a claim the operating system keeps track of: one program says "this file
is mine right now", and any other program that asks can find that out. Hydra asks for the
same lock. If it cannot have it, something else is using the card. The mechanism is
called `fcntl.flock`, and it exists on Unix style systems but not on Windows.

So on Windows, Hydra logs a loud warning and routes the work as though the card were
free. Everything else on Windows works normally. The tests for this feature skip on
Windows for the same reason, and only on Windows.

